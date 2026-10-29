"""Ability modifiers and one-shot character validation for profiles with manual stat entry.

Profiles that roll stats, hit points or talents must use the server-rolled draft in
``lk_rules.creation``; ``validate_character`` refuses them rather than letting a client supply
values that should have been rolled.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .abilities import Ability
from .creation import (
    CharacterBuild,
    CreationDraft,
    _origin_resolved,
    _roll_basis,
    choose_origin,
    finalize,
    set_manual_scores,
)
from .definitions import ClassDef, HitPointRule, StatGeneration
from .errors import UnverifiedRule, ValidationFailed
from .dice import DiceExpression
from .inventory import Inventory, ItemStack, capacity_slots, check_capacity
from .profile import ModifierBand, RuleRef, RulesProfile


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
    """Player-supplied input for manual-entry profiles. Everything derived is computed, never accepted."""

    name: str = Field(min_length=1, max_length=80)
    class_key: str = Field(min_length=1, max_length=64)
    ancestry_key: str | None = Field(default=None, max_length=64)
    ability_scores: AbilityScores
    starting_items: list[StartingItem] = Field(default_factory=list, max_length=50)
    alignment: str | None = Field(default=None, max_length=32)
    trait_choices: dict[str, dict[str, Any]] = Field(default_factory=dict)
    languages: list[str] = Field(default_factory=list, max_length=10)


class CharacterSheet(BaseModel):
    model_config = ConfigDict(frozen=True)
    build: CharacterBuild
    inventory: Inventory


def build_inventory(items: list[StartingItem], profile: RulesProfile, build: CharacterBuild) -> Inventory:
    issues: list[str] = []
    stacks: list[ItemStack] = []
    for wanted in items:
        item_def, _ = profile.item(wanted.item_key)
        if item_def is None:
            issues.append(f"unknown item {wanted.item_key!r}")
            continue
        stacks.append(ItemStack(item_key=item_def.key, name=item_def.name, quantity=wanted.quantity,
                                slots_each=item_def.slots))
    if issues:
        raise ValidationFailed(issues)
    inventory = Inventory(stacks=tuple(stacks))
    check_capacity(inventory, profile, scores=build.scores,
                   bonus_slots=build.gear_slots - _base_capacity(profile, build))
    return inventory


def _base_capacity(profile: RulesProfile, build: CharacterBuild) -> int:
    return capacity_slots(profile, scores=build.scores)[0]


def validate_character(spec: CharacterSpec, profile: RulesProfile) -> CharacterSheet:
    """Validate a manual-entry character, collecting every issue before failing."""
    gen, _ = profile.optional("stats.generation", StatGeneration)
    hp_rule, _ = profile.get("character.hit_points", HitPointRule)
    classes, _ = profile.get("character.classes", list[ClassDef])
    klass = next((c for c in classes if c.key == spec.class_key), None)
    if (gen and gen.method != "manual") or hp_rule.level_1 != "max_hit_die" or (
            klass and klass.talent_table and klass.talent_rolls_level_1):
        raise ValidationFailed(["this rules profile rolls stats, hit points or talents; use the creation draft"])
    issues: list[str] = []
    draft = CreationDraft(stage="stats", stat_method="manual")
    try:
        draft = set_manual_scores(draft, spec.ability_scores.model_dump(), profile)
    except ValidationFailed as exc:
        issues.extend(exc.issues)
        draft = draft.model_copy(update={"base_scores": spec.ability_scores.model_dump(), "stage": "origin"})
    try:
        draft = choose_origin(draft, profile, ancestry_key=spec.ancestry_key, class_key=spec.class_key)
    except ValidationFailed as exc:
        issues.extend(exc.issues)
    for wanted in spec.starting_items:
        if profile.item(wanted.item_key)[0] is None:
            issues.append(f"unknown item {wanted.item_key!r}")
    if issues:
        raise ValidationFailed(issues)
    assert klass is not None
    draft = draft.model_copy(update={"stage": "rolled", "hp_roll_total": _hit_die_max(klass), "name": spec.name,
                                     "alignment": spec.alignment, "trait_choices": spec.trait_choices,
                                     "languages": spec.languages})
    # Nothing is rolled on this path, so the roll basis is simply the current origin choices.
    origin = [m for r in _origin_resolved(draft, profile, []) for m in r.modifiers]
    draft = draft.model_copy(update={"roll_basis": _roll_basis(origin)})
    build = finalize(draft, profile)
    return CharacterSheet(build=build, inventory=build_inventory(spec.starting_items, profile, build))


def _hit_die_max(klass: ClassDef) -> int:
    return DiceExpression.parse(klass.hit_die).terms[0].sides
