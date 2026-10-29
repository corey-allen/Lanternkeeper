"""Typed modifiers: the one mechanism by which ancestries, class features, talents, items and spells
change rolls and derived values.

A ``Modifier`` says *what* it affects (target), *how* (a flat or derived bonus, extra dice, or
advantage/disadvantage), *when* (scope filters such as weapon type or check tag), and *where it came
from* (source), so every adjustment to a roll can be shown and audited. Narrative abilities that the
engine cannot resolve are ``Feature``s, not modifiers.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .abilities import Ability


class ModTarget(StrEnum):
    ATTACK_ROLL = "ATTACK_ROLL"
    DAMAGE_ROLL = "DAMAGE_ROLL"
    DAMAGE_DICE = "DAMAGE_DICE"  # extra weapon dice
    SPELLCASTING_CHECK = "SPELLCASTING_CHECK"
    STAT_CHECK = "STAT_CHECK"
    STAT_SCORE = "STAT_SCORE"  # folded into the score when the character is finalised
    ARMOR_CLASS = "ARMOR_CLASS"
    HIT_POINTS = "HIT_POINTS"  # flat change to max HP
    HIT_POINT_ROLL = "HIT_POINT_ROLL"  # e.g. advantage on hit point dice
    GEAR_SLOTS = "GEAR_SLOTS"
    INITIATIVE = "INITIATIVE"
    TALENT_ROLLS_LEVEL_1 = "TALENT_ROLLS_LEVEL_1"


class ModMode(StrEnum):
    BONUS = "BONUS"
    ADVANTAGE = "ADVANTAGE"
    DISADVANTAGE = "DISADVANTAGE"


class Scope(BaseModel):
    """All set fields must match the roll's context for the modifier to apply."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    attack_kind: Literal["melee", "ranged"] | None = None
    weapon_type: str | None = None
    armor_type: str | None = None
    stat: Ability | None = None
    tradition: str | None = None
    spell: str | None = None
    check_tag: str | None = None
    condition: str | None = None  # e.g. "target_unaware"

    def matches(self, context: Scope) -> bool:
        for field, wanted in self.model_dump(exclude_none=True).items():
            if getattr(context, field) != wanted:
                return False
        return True


class Modifier(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    target: ModTarget
    mode: ModMode = ModMode.BONUS
    amount: int = 0
    amount_from_stat: Ability | None = None  # add that stat's modifier
    positive_only: bool = False  # only add amount_from_stat when it is positive
    per_level_divisor: int | None = Field(default=None, ge=1)  # add floor(level / divisor)
    scope: Scope = Scope()
    source: str = Field(min_length=1, max_length=120)  # e.g. "ancestry:dwarf/stout"
    label: str = ""


class FeatureKind(StrEnum):
    IMMUNITY_SURPRISE = "IMMUNITY_SURPRISE"
    LIMITED_USE = "LIMITED_USE"  # an ability with uses per day/rest; effect needs a ruling or later support
    GRANTED_SPELL = "GRANTED_SPELL"
    TRAINED_TASKS = "TRAINED_TASKS"  # task categories that auto-succeed when there is no pressure (ADR-0009)
    FREE_ITEM = "FREE_ITEM"  # carried without using gear slots
    LEARN_FROM_SCROLLS = "LEARN_FROM_SCROLLS"
    NARRATIVE = "NARRATIVE"


class Feature(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str = Field(min_length=1, max_length=64)
    name: str
    kind: FeatureKind
    uses_per: Literal["day", "rest"] | None = None
    uses: int | None = Field(default=None, ge=1)
    tags: tuple[str, ...] = ()
    source: str = ""
    text_ref: str = ""  # page reference only; rules text is never stored in the repo


class Applied(BaseModel):
    model_config = ConfigDict(frozen=True)
    source: str
    label: str
    amount: int


class Collected(BaseModel):
    """The net effect of every applicable modifier on one roll, with the breakdown for audit."""

    model_config = ConfigDict(frozen=True)
    bonus: int
    advantage: bool
    disadvantage: bool
    extra_dice: int
    applied: tuple[Applied, ...]

    @property
    def roll_mode(self) -> Literal["normal", "advantage", "disadvantage"]:
        # Advantage and disadvantage cancel each other out.
        if self.advantage and not self.disadvantage:
            return "advantage"
        if self.disadvantage and not self.advantage:
            return "disadvantage"
        return "normal"


def amount_of(mod: Modifier, *, stat_mods: dict[Ability, int], level: int) -> int:
    total = mod.amount
    if mod.amount_from_stat is not None:
        value = stat_mods[mod.amount_from_stat]
        total += max(0, value) if mod.positive_only else value
    if mod.per_level_divisor:
        total += level // mod.per_level_divisor
    return total


def collect(mods: list[Modifier] | tuple[Modifier, ...], target: ModTarget, context: Scope, *,
            stat_mods: dict[Ability, int], level: int) -> Collected:
    bonus = extra = 0
    adv = dis = False
    applied: list[Applied] = []
    for mod in mods:
        if mod.target != target or not mod.scope.matches(context):
            continue
        value = amount_of(mod, stat_mods=stat_mods, level=level)
        if mod.mode == ModMode.ADVANTAGE:
            adv = True
        elif mod.mode == ModMode.DISADVANTAGE:
            dis = True
        elif target == ModTarget.DAMAGE_DICE:
            extra += value
        else:
            bonus += value
        applied.append(Applied(source=mod.source, label=mod.label or mod.target.value, amount=value))
    return Collected(bonus=bonus, advantage=adv, disadvantage=dis, extra_dice=extra, applied=tuple(applied))
