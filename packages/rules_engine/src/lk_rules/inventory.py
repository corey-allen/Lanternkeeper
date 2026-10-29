"""Pure inventory operations. Every function returns a new Inventory."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .errors import InventoryError
from .profile import InventoryCapacity, RuleRef, RulesProfile


class ItemStack(BaseModel):
    model_config = ConfigDict(frozen=True)
    item_key: str
    name: str
    quantity: int = Field(ge=1)
    slots_each: int = Field(ge=0)
    equipped: bool = False

    @property
    def slots(self) -> int:
        return self.quantity * self.slots_each


class Inventory(BaseModel):
    model_config = ConfigDict(frozen=True)
    stacks: tuple[ItemStack, ...] = ()

    def quantity_of(self, item_key: str) -> int:
        return sum(s.quantity for s in self.stacks if s.item_key == item_key)

    @property
    def slots_used(self) -> int:
        return sum(s.slots for s in self.stacks)


def check_capacity(inventory: Inventory, profile: RulesProfile) -> RuleRef:
    capacity, ref = profile.get("inventory.capacity", InventoryCapacity)
    if inventory.slots_used > capacity.slots:
        raise InventoryError(
            f"Inventory needs {inventory.slots_used} slots but capacity is {capacity.slots}",
            slots_used=inventory.slots_used,
            capacity=capacity.slots,
        )
    return ref


def add_item(inventory: Inventory, item_key: str, quantity: int, profile: RulesProfile) -> Inventory:
    if quantity < 1:
        raise InventoryError("Quantity must be positive", quantity=quantity)
    item_def, _ = profile.item(item_key)
    if item_def is None:
        raise InventoryError(f"Unknown item {item_key!r}", item_key=item_key)
    stacks = list(inventory.stacks)
    for i, stack in enumerate(stacks):
        if stack.item_key == item_key:
            stacks[i] = stack.model_copy(update={"quantity": stack.quantity + quantity})
            break
    else:
        stacks.append(ItemStack(item_key=item_key, name=item_def.name, quantity=quantity,
                                slots_each=item_def.slots))
    result = Inventory(stacks=tuple(stacks))
    check_capacity(result, profile)
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
