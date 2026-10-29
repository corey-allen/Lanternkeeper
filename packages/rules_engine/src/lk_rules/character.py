"""Ability modifiers and character validation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .errors import UnverifiedRule, ValidationFailed
from .inventory import Inventory, ItemStack, check_capacity
from .profile import (
    Ability,
    ArmorClassRule,
    ClassDef,
    ModifierBand,
    RuleRef,
    RulesProfile,
    ScoreRange,
)


class AbilityScores(BaseModel):
    model_config = ConfigDict(frozen=True)
    STR: int
    DEX: int
    CON: int
    INT: int
    WIS: int
    CHA: int

    def score(self, ability: Ability) -> int:
        return getattr(self, ability.value)


def ability_modifier(score: int, profile: RulesProfile) -> tuple[int, RuleRef]:
    table, ref = profile.get("ability.modifier_table", list[ModifierBand])
    for band in table:
        if band.min <= score <= band.max:
            return band.modifier, ref
    raise UnverifiedRule(f"ability.modifier_table[{score}]", profile.key, "MISSING")


class StartingItem(BaseModel):
    item_key: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=999)


class CharacterSpec(BaseModel):
    """Player-supplied character input. Everything derived is computed, never accepted."""

    name: str = Field(min_length=1, max_length=80)
    class_key: str = Field(min_length=1, max_length=64)
    ability_scores: AbilityScores
    starting_items: list[StartingItem] = Field(default_factory=list, max_length=50)


class CharacterSheet(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    class_key: str
    class_name: str
    level: int
    ability_scores: AbilityScores
    modifiers: dict[Ability, int]
    max_hp: int
    current_hp: int
    armor_class: int
    inventory: Inventory
    rule_refs: tuple[str, ...]


def validate_character(spec: CharacterSpec, profile: RulesProfile) -> CharacterSheet:
    """Validate a character against the profile, collecting every issue before failing."""
    issues: list[str] = []
    refs: list[RuleRef] = []

    score_range, ref = profile.get("ability.score_range", ScoreRange)
    refs.append(ref)
    for ability in Ability:
        value = spec.ability_scores.score(ability)
        if not score_range.min <= value <= score_range.max:
            issues.append(
                f"{ability.value} {value} outside allowed range {score_range.min}-{score_range.max}"
            )

    classes, ref = profile.get("character.classes", list[ClassDef])
    refs.append(ref)
    class_def = next((c for c in classes if c.key == spec.class_key), None)
    if class_def is None:
        issues.append(f"unknown class {spec.class_key!r}; allowed: {[c.key for c in classes]}")

    items: list[ItemStack] = []
    for wanted in spec.starting_items:
        item_def, ref = profile.item(wanted.item_key)
        if item_def is None:
            issues.append(f"unknown item {wanted.item_key!r}")
            continue
        items.append(
            ItemStack(item_key=item_def.key, name=item_def.name, quantity=wanted.quantity,
                      slots_each=item_def.slots)
        )
    if spec.starting_items:
        refs.append(profile.ref("items.catalog"))

    if issues:
        raise ValidationFailed(issues)

    modifiers: dict[Ability, int] = {}
    for ability in Ability:
        modifiers[ability], ref = ability_modifier(spec.ability_scores.score(ability), profile)
    refs.append(ref)

    inventory = Inventory(stacks=tuple(items))
    capacity_ref = check_capacity(inventory, profile)
    refs.append(capacity_ref)

    assert class_def is not None
    max_hp = max(1, class_def.base_hit_points + modifiers[Ability.CON])

    ac_rule, ref = profile.get("character.armor_class", ArmorClassRule)
    refs.append(ref)
    armor_class = ac_rule.base + (modifiers[ac_rule.add_ability] if ac_rule.add_ability else 0)

    return CharacterSheet(
        name=spec.name.strip(),
        class_key=class_def.key,
        class_name=class_def.name,
        level=1,
        ability_scores=spec.ability_scores,
        modifiers=modifiers,
        max_hp=max_hp,
        current_hp=max_hp,
        armor_class=armor_class,
        inventory=inventory,
        rule_refs=tuple(dict.fromkeys(str(r) for r in refs)),
    )
