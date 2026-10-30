"""Gear: slots, kits, coins, buying, proficiency, equipping, AC, weapon profiles (dev house rules)."""

from __future__ import annotations

import pytest

from lk_rules import DEV_HOUSE_RULES as P
from lk_rules import Ability
from lk_rules.definitions import ClassDef
from lk_rules.errors import InventoryError, ValidationFailed
from lk_rules.gear import (
    Purchase,
    Purse,
    armor_class,
    buy,
    coin_slots,
    equip,
    pay,
    proficient,
    unequip,
    weapon_profile,
)
from lk_rules.inventory import Inventory, add_item
from lk_rules.modifiers import Modifier, ModTarget, Scope

MODS = {a: 0 for a in Ability} | {Ability.DEX: 2, Ability.STR: 1}
FULL = ClassDef(key="any", name="Any", hit_die="1d6")
NO_ARMOR = ClassDef(key="mage", name="Mage", hit_die="1d4", weapons=("walking_staff",), armor=(), shields=False)


def test_kits_expand_and_free_items():
    inv = add_item(Inventory(), "travel_kit", 1, P)
    assert inv.quantity_of("torch") == 2 and inv.quantity_of("pack") == 1 and inv.get("travel_kit") is None
    assert inv.slots_used == 0 + 2 + 1 + 1  # pack free, 2 torches, 2 rations in one slot, rope


def test_coins_take_slots_beyond_the_free_amount():
    assert coin_slots(Purse(coins={"gp": 100}), P) == 0
    assert coin_slots(Purse(coins={"gp": 150, "sp": 60}), P) == 2


def test_pay_exact_and_with_change():
    assert pay(Purse(coins={"gp": 3}), 2, "gp", P).coins == {"gp": 1}
    assert pay(Purse(coins={"gp": 1}), 3, "sp", P).coins == {"sp": 7}  # change made, fewest coins
    with pytest.raises(InventoryError):
        pay(Purse(coins={"sp": 2}), 1, "gp", P)


def test_buy_collects_every_problem():
    inv, purse = buy(Inventory(), Purse(coins={"gp": 20}), [Purchase(item_key="short_blade", quantity=1),
                                                           Purchase(item_key="travel_kit", quantity=1)],
                     P, capacity=10)
    assert purse.coins == {"gp": 12} and inv.quantity_of("short_blade") == 1
    with pytest.raises(ValidationFailed) as exc:
        buy(Inventory(), Purse(coins={"gp": 1}), [Purchase(item_key="padded_coat", quantity=1),
                                                  Purchase(item_key="unicorn", quantity=1)], P, capacity=10)
    assert len(exc.value.issues) == 2
    with pytest.raises(ValidationFailed, match="capacity"):
        buy(Inventory(), Purse(coins={"gp": 50}), [Purchase(item_key="rope", quantity=11)], P, capacity=10)


def test_proficiency_and_equipping():
    inv = add_item(add_item(add_item(Inventory(), "padded_coat", 1, P), "buckler", 1, P), "walking_staff", 1, P)
    assert not proficient(P.item("padded_coat")[0], NO_ARMOR) and proficient(P.item("walking_staff")[0], NO_ARMOR)
    with pytest.raises(ValidationFailed, match="cannot use"):
        equip(inv, "padded_coat", P, NO_ARMOR)
    inv = equip(equip(inv, "padded_coat", P, FULL), "buckler", P, FULL)
    inv = equip(inv, "walking_staff", P, FULL)
    assert inv.get("buckler").equipped_as == "shield"
    inv = equip(inv, "walking_staff", P, FULL, hands="both_hands")  # two hands frees the shield
    assert inv.get("buckler").equipped_as is None and inv.get("walking_staff").equipped_as == "both_hands"
    with pytest.raises(ValidationFailed, match="cannot be equipped"):
        equip(add_item(inv, "rope", 1, P), "rope", P, FULL)
    with pytest.raises(InventoryError, match="not carrying"):
        equip(inv, "sling", P, FULL)
    assert unequip(inv, "padded_coat").get("padded_coat").equipped_as is None


def test_armor_class_from_armor_shield_and_modifiers():
    inv = add_item(add_item(Inventory(), "padded_coat", 1, P), "buckler", 1, P)
    assert armor_class(inv, P, stat_mods=MODS)[0] == 10 + 2  # unarmored rule
    inv = equip(equip(inv, "padded_coat", P, FULL), "buckler", P, FULL)
    ac, sources = armor_class(inv, P, stat_mods=MODS)
    assert ac == 11 + 2 + 1 and "armor:padded_coat" in sources
    bonus = Modifier(target=ModTarget.ARMOR_CLASS, amount=1, scope=Scope(armor_type="padded_coat"), source="talent")
    other = Modifier(target=ModTarget.ARMOR_CLASS, amount=5, scope=Scope(armor_type="plate"), source="nope")
    assert armor_class(inv, P, stat_mods=MODS, modifiers=[bonus, other])[0] == 15


def test_weapon_profiles():
    blade = weapon_profile(P.item("short_blade")[0], profile=P, stat_mods=MODS, hands="main_hand")
    assert blade.stat == Ability.DEX and blade.attack_kind == "melee"  # finesse uses the better stat
    staff = weapon_profile(P.item("walking_staff")[0], profile=P, stat_mods=MODS, hands="both_hands")
    assert staff.stat == Ability.STR and staff.damage == "1d6"
    sling = weapon_profile(P.item("sling")[0], profile=P, stat_mods=MODS, hands="main_hand")
    assert sling.attack_kind == "ranged" and sling.stat == Ability.DEX
    with pytest.raises(ValidationFailed):
        weapon_profile(P.item("short_blade")[0], profile=P, stat_mods=MODS, hands=None, prefer="ranged")
