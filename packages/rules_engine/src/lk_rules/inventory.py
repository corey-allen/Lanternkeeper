"""Pure inventory operations. Every function returns a new Inventory.

Slot arithmetic follows the item definition: ``quantity_per_slot`` items share one slot unit
(``slots`` gear slots), and the first ``free_quantity`` of an item cost no slots. Coins are counted
separately (see ``lk_rules.gear``) and passed in as extra slots.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field

from .definitions import ItemDef
from .errors import InventoryError
from .profile import InventoryCapacity, RuleRef, RulesProfile


class ItemStack(BaseModel):
    model_config = ConfigDict(frozen=True)
    item_key: str
    name: str
    quantity: int = Field(ge=1)
    slots_each: int = Field(ge=0)  # gear slots per slot unit
    quantity_per_slot: int = Field(default=1, ge=1)
    free_quantity: int = Field(default=0, ge=0)
    equipped_as: str | None = None  # "armor", "shield", "main_hand", "off_hand", "both_hands"
    equipped: bool = False  # legacy flag kept in sync with equipped_as

    @property
    def slots(self) -> int:
        billable = max(0, self.quantity - self.free_quantity)
        return math.ceil(billable / self.quantity_per_slot) * self.slots_each


def stack_for(item: ItemDef, quantity: int) -> ItemStack:
    return ItemStack(item_key=item.key, name=item.name, quantity=quantity, slots_each=item.slots,
                     quantity_per_slot=item.quantity_per_slot, free_quantity=item.free_quantity)


class Inventory(BaseModel):
    model_config = ConfigDict(frozen=True)
    stacks: tuple[ItemStack, ...] = ()

    def quantity_of(self, item_key: str) -> int:
        return sum(s.quantity for s in self.stacks if s.item_key == item_key)

    def get(self, item_key: str) -> ItemStack | None:
        return next((s for s in self.stacks if s.item_key == item_key), None)

    @property
    def slots_used(self) -> int:
        return sum(s.slots for s in self.stacks)


def capacity_slots(profile: RulesProfile, *, scores: dict[str, int] | None = None,
                   bonus_slots: int = 0) -> tuple[int, RuleRef]:
    """Total gear slots: a fixed number, or a stat score with a floor, plus modifier bonuses."""
    rule, ref = profile.get("inventory.capacity", InventoryCapacity)
    if rule.method == "fixed_slots":
        base = rule.slots or 0
    else:
        if scores is None or rule.stat is None:
            raise InventoryError("Capacity depends on a stat score that was not provided")
        base = max(scores[rule.stat.value], rule.floor or 0)
    return base + bonus_slots, ref


def check_capacity(inventory: Inventory, profile: RulesProfile, *, scores: dict[str, int] | None = None,
                   bonus_slots: int = 0, capacity: int | None = None, extra_slots: int = 0) -> RuleRef:
    """Raise if the inventory (plus ``extra_slots``, e.g. coins) exceeds capacity.

    ``capacity`` may be given directly (a character's recorded gear slots); otherwise it is derived.
    """
    ref = profile.ref("inventory.capacity")
    if capacity is None:
        capacity, ref = capacity_slots(profile, scores=scores, bonus_slots=bonus_slots)
    used = inventory.slots_used + extra_slots
    if used > capacity:
        raise InventoryError(f"Inventory needs {used} gear slots but capacity is {capacity}",
                             slots_used=used, capacity=capacity)
    return ref


def _expand(item_key: str, quantity: int, profile: RulesProfile) -> list[tuple[ItemDef, int]]:
    item, _ = profile.item(item_key)
    if item is None:
        raise InventoryError(f"Unknown item {item_key!r}", item_key=item_key)
    if not item.contents:
        return [(item, quantity)]
    out = []
    for part in item.contents:
        out += _expand(part.item_key, part.quantity * quantity, profile)
    return out


def add_item(inventory: Inventory, item_key: str, quantity: int, profile: RulesProfile, *,
             scores: dict[str, int] | None = None, bonus_slots: int = 0, capacity: int | None = None,
             extra_slots: int = 0, check: bool = True) -> Inventory:
    """Add an item (kits expand into their contents) and check capacity."""
    if quantity < 1:
        raise InventoryError("Quantity must be positive", quantity=quantity)
    stacks = list(inventory.stacks)
    for item, qty in _expand(item_key, quantity, profile):
        for i, stack in enumerate(stacks):
            if stack.item_key == item.key:
                stacks[i] = stack.model_copy(update={"quantity": stack.quantity + qty})
                break
        else:
            stacks.append(stack_for(item, qty))
    result = Inventory(stacks=tuple(stacks))
    if check:
        check_capacity(result, profile, scores=scores, bonus_slots=bonus_slots, capacity=capacity,
                       extra_slots=extra_slots)
    return result


def remove_item(inventory: Inventory, item_key: str, quantity: int) -> Inventory:
    if quantity < 1:
        raise InventoryError("Quantity must be positive", quantity=quantity)
    have = inventory.quantity_of(item_key)
    if have < quantity:
        raise InventoryError(
            f"Not enough {item_key}: have {have}, need {quantity}",
            item_key=item_key, have=have, need=quantity,
        )
    remaining = quantity
    stacks: list[ItemStack] = []
    for stack in inventory.stacks:
        if stack.item_key == item_key and remaining:
            take = min(stack.quantity, remaining)
            remaining -= take
            if stack.quantity - take:
                stacks.append(stack.model_copy(update={"quantity": stack.quantity - take}))
        else:
            stacks.append(stack)
    return Inventory(stacks=tuple(stacks))
