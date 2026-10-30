"""Coins, buying, proficiency, equipping, armor class and weapon attack profiles.

Making change: when a cost cannot be paid in exact coins, the purse is converted and the remainder
is held in the fewest coins (largest denominations first). This is a Lanternkeeper convenience,
not a rule from any game book.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .abilities import Ability
from .definitions import AttackStatsRule, ClassDef, CurrencyDef, ItemDef
from .errors import InventoryError, ValidationFailed
from .inventory import Inventory, ItemStack, add_item, check_capacity
from .modifiers import Modifier, ModTarget, Scope, collect
from .profile import ArmorClassRule, RuleRef, RulesProfile

Hands = Literal["main_hand", "both_hands"]


class Purse(BaseModel):
    model_config = ConfigDict(frozen=True)
    coins: dict[str, int] = Field(default_factory=dict)

    def value(self, currency: CurrencyDef) -> int:
        rates = {d.key: d.value for d in currency.denominations}
        return sum(rates[k] * v for k, v in self.coins.items())

    def count(self) -> int:
        return sum(self.coins.values())


def currency(profile: RulesProfile) -> tuple[CurrencyDef, RuleRef]:
    return profile.get("items.currency", CurrencyDef)


def coin_slots(purse: Purse, profile: RulesProfile) -> int:
    cur, _ = currency(profile)
    billable = max(0, purse.count() - cur.free_coins)
    return math.ceil(billable / cur.coins_per_slot)


def normalize(value: int, cur: CurrencyDef) -> Purse:
    coins: dict[str, int] = {}
    for d in sorted(cur.denominations, key=lambda d: -d.value):
        coins[d.key], value = divmod(value, d.value)
    return Purse(coins={k: v for k, v in coins.items() if v})


def pay(purse: Purse, amount: int, unit: str, profile: RulesProfile) -> Purse:
    cur, _ = currency(profile)
    rates = {d.key: d.value for d in cur.denominations}
    if unit not in rates:
        raise ValidationFailed([f"unknown coin {unit!r}"])
    have = dict(purse.coins)
    if have.get(unit, 0) >= amount:  # exact coins: no change needed
        have[unit] -= amount
        return Purse(coins={k: v for k, v in have.items() if v})
    cost = amount * rates[unit]
    total = purse.value(cur)
    if total < cost:
        raise InventoryError(f"Not enough money: costs {amount} {unit}", cost=cost, have=total)
    return normalize(total - cost, cur)


class Purchase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    item_key: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=99)


def buy(inventory: Inventory, purse: Purse, purchases: list[Purchase], profile: RulesProfile, *,
        capacity: int) -> tuple[Inventory, Purse]:
    """Buy items in order, collecting every problem before failing."""
    issues: list[str] = []
    for p in purchases:
        item, _ = profile.item(p.item_key)
        if item is None:
            issues.append(f"unknown item {p.item_key!r}")
            continue
        if item.cost is None:
            issues.append(f"{item.name} cannot be bought")
            continue
        try:
            purse = pay(purse, item.cost.amount * p.quantity, item.cost.unit, profile)
            inventory = add_item(inventory, item.key, p.quantity, profile, check=False)
        except (InventoryError, ValidationFailed) as exc:
            issues.append(f"{item.name}: {exc.message}")
    if not issues:
        try:
            check_capacity(inventory, profile, capacity=capacity, extra_slots=coin_slots(purse, profile))
        except InventoryError as exc:
            issues.append(exc.message)
    if issues:
        raise ValidationFailed(issues)
    return inventory, purse


# ---- proficiency and equipping ------------------------------------------------------------


def proficient(item: ItemDef, klass: ClassDef | None) -> bool:
    if klass is None:
        return True
    if item.armor and item.armor.kind == "shield":
        return klass.shields
    if item.armor:
        return "all" in klass.armor or item.key in klass.armor
    if item.weapon:
        return "all" in klass.weapons or item.key in klass.weapons
    return True


def equip(inventory: Inventory, item_key: str, profile: RulesProfile, klass: ClassDef | None, *,
          hands: Hands = "main_hand") -> Inventory:
    stack = inventory.get(item_key)
    item, _ = profile.item(item_key)
    if stack is None or item is None:
        raise InventoryError("You are not carrying that item", item_key=item_key)
    if not (item.armor or item.weapon):
        raise ValidationFailed([f"{item.name} cannot be equipped"])
    if not proficient(item, klass):
        raise ValidationFailed([f"Your class cannot use {item.name}"])
    if item.armor and item.armor.kind == "shield":
        role = "shield"
    elif item.armor:
        role = "armor"
    else:
        props = item.weapon.properties
        role = "both_hands" if "two_handed" in props else hands
        if role == "both_hands" and not ({"two_handed", "versatile"} & set(props)):
            raise ValidationFailed([f"{item.name} is wielded in one hand"])
    stacks: list[ItemStack] = []
    for s in inventory.stacks:
        clear = (s.equipped_as == role or (role in ("main_hand", "both_hands") and s.equipped_as in ("main_hand", "both_hands"))
                 or (role == "both_hands" and s.equipped_as == "shield") or (role == "shield" and s.equipped_as == "both_hands"))
        if clear and s.item_key != item_key:
            s = s.model_copy(update={"equipped_as": None, "equipped": False})
        if s.item_key == item_key:
            s = s.model_copy(update={"equipped_as": role, "equipped": True})
        stacks.append(s)
    return Inventory(stacks=tuple(stacks))


def unequip(inventory: Inventory, item_key: str) -> Inventory:
    return Inventory(stacks=tuple(s.model_copy(update={"equipped_as": None, "equipped": False})
                                  if s.item_key == item_key else s for s in inventory.stacks))


def equipped(inventory: Inventory, role: str) -> ItemStack | None:
    return next((s for s in inventory.stacks if s.equipped_as == role), None)


def armor_class(inventory: Inventory, profile: RulesProfile, *, stat_mods: dict[Ability, int],
                modifiers: list[Modifier] | tuple[Modifier, ...] = (), level: int = 1) -> tuple[int, list[str]]:
    """AC from worn armor (or the unarmored rule), shield, and ARMOR_CLASS modifiers. Returns (ac, sources)."""
    rule, ref = profile.get("character.armor_class", ArmorClassRule)
    sources = [str(ref)]
    body = equipped(inventory, "armor")
    body_def = profile.item(body.item_key)[0] if body else None
    if body_def and body_def.armor and body_def.armor.base_ac is not None:
        ac = body_def.armor.base_ac + (stat_mods[Ability.DEX] if body_def.armor.add_dex else 0)
        sources.append(f"armor:{body_def.key}")
    else:
        ac = rule.base + (stat_mods[rule.add_ability] if rule.add_ability else 0)
    shield = equipped(inventory, "shield")
    shield_def = profile.item(shield.item_key)[0] if shield else None
    if shield_def and shield_def.armor:
        ac += shield_def.armor.ac_bonus
        sources.append(f"shield:{shield_def.key}")
    contexts = [Scope()] + [Scope(armor_type=k) for k in (body_def.key if body_def else None,
                                                         shield_def.key if shield_def else None) if k]
    for ctx in contexts:
        got = collect([m for m in modifiers if m.scope.armor_type == ctx.armor_type], ModTarget.ARMOR_CLASS, ctx,
                      stat_mods=stat_mods, level=level)
        ac += got.bonus
        sources += [a.source for a in got.applied]
    return ac, sources


class WeaponProfile(BaseModel):
    model_config = ConfigDict(frozen=True)
    item_key: str
    name: str
    attack_kind: Literal["melee", "ranged"]
    stat: Ability
    damage: str
    ranges: tuple[str, ...]


def weapon_profile(item: ItemDef, *, profile: RulesProfile, stat_mods: dict[Ability, int],
                   hands: str | None, prefer: Literal["melee", "ranged"] | None = None) -> WeaponProfile:
    """How this weapon attacks: melee or ranged, which stat, which damage die."""
    if item.weapon is None:
        raise ValidationFailed([f"{item.name} is not a weapon"])
    w = item.weapon
    kind: Literal["melee", "ranged"] = prefer or ("ranged" if w.kind == "ranged" else "melee")
    if w.kind == "melee" and kind == "ranged" and "thrown" not in w.properties:
        raise ValidationFailed([f"{item.name} cannot be used at range"])
    if w.kind == "ranged" and kind == "melee":
        raise ValidationFailed([f"{item.name} is a ranged weapon"])
    if w.attack_ability is not None:
        stat = w.attack_ability
    else:
        rule, _ = profile.get("combat.attack_stats", AttackStatsRule)
        stat = rule.melee if kind == "melee" else rule.ranged
        flexible = "finesse" in w.properties or (kind == "ranged" and "thrown" in w.properties)
        if flexible and rule.either:
            stat = max(rule.either, key=lambda a: stat_mods[a])
    damage = w.damage_two_handed if (hands == "both_hands" and w.damage_two_handed) else w.damage
    return WeaponProfile(item_key=item.key, name=item.name, attack_kind=kind, stat=stat, damage=damage,
                         ranges=w.ranges)
