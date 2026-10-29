"""Rules profiles: every numeric rule value lives here, with provenance and status.

The engine never hard-codes game-system values. A mechanic asks the active
profile for the rule it needs; if the rule is missing or UNVERIFIED, the
mechanic fails with :class:`UnverifiedRule` instead of guessing.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from enum import StrEnum
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .abilities import Ability
from .definitions import (
    AncestryDef,
    CapacityRule,
    ClassDef,
    DeityDef,
    DiceAmount,
    HitPointRule,
    LanguagesDef,
    NamedEntry,
    StatGeneration,
    TitleRow,
)
from .errors import UnverifiedRule

T = TypeVar("T")


class RuleStatus(StrEnum):
    VERIFIED = "VERIFIED"  # checked against a cited source and cleared by licensing review
    HOUSE_RULE = "HOUSE_RULE"  # explicitly adopted by the user; not an official rule
    UNVERIFIED = "UNVERIFIED"  # placeholder; using it raises UnverifiedRule


class RuleEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: RuleStatus
    source: str = Field(min_length=1, description="Citation, or 'house rule: <who/when>'.")
    value: Any = None
    notes: str = ""
    verified_on: date | None = None  # when the owner checked a VERIFIED value against the source


# ---- typed rule values -------------------------------------------------------


class ScoreRange(BaseModel):
    min: int
    max: int


class ModifierBand(BaseModel):
    min: int
    max: int
    modifier: int


class WeaponDef(BaseModel):
    damage: str
    attack_ability: Ability


class LightDef(BaseModel):
    duration_minutes: int = Field(ge=1)


class ItemDef(BaseModel):
    key: str
    name: str
    slots: int = Field(ge=0)
    weapon: WeaponDef | None = None
    light: LightDef | None = None
    consumable: bool = False


class ArmorClassRule(BaseModel):
    base: int
    add_ability: Ability | None = None


class InitiativeRule(BaseModel):
    ability: Ability


class AttackRule(BaseModel):
    hit_when: Literal["total_gte_armor_class"]
    minimum_damage: int = Field(ge=0)


class ZeroHpRule(BaseModel):
    character_outcome: Literal["incapacitated"]
    creature_outcome: Literal["defeated"]


RULE_TYPES: dict[str, Any] = {
    "ability.score_range": ScoreRange,
    "ability.modifier_table": list[ModifierBand],
    "check.difficulty": dict[str, int],
    "check.advantage_enabled": bool,
    "stats.generation": StatGeneration,
    "character.ancestries": list[AncestryDef],
    "character.classes": list[ClassDef],
    "character.hit_points": HitPointRule,
    "character.languages": LanguagesDef,
    "character.alignments": list[str],
    "character.deities": list[DeityDef],
    "character.backgrounds": list[NamedEntry],
    "character.titles": list[TitleRow],
    "character.starting_gold": DiceAmount,
    "character.armor_class": ArmorClassRule,
    "items.catalog": list[ItemDef],
    "inventory.capacity": CapacityRule,
    "time.action_minutes": dict[str, int],
    "combat.initiative": InitiativeRule,
    "combat.attack": AttackRule,
    "combat.zero_hp": ZeroHpRule,
    "conditions.catalog": list[str],
}


class RuleRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    profile_key: str
    profile_version: str
    rule_key: str
    status: RuleStatus
    source: str

    def __str__(self) -> str:
        return f"{self.profile_key}@{self.profile_version}:{self.rule_key}"


class RulesProfile(BaseModel):
    model_config = ConfigDict(frozen=True)
    key: str
    name: str
    version: str
    system: str
    description: str
    requires_house_rule_acceptance: bool
    entries: dict[str, RuleEntry]

    def get(self, rule_key: str, type_: type[T] | Any) -> tuple[T, RuleRef]:
        entry = self.entries.get(rule_key)
        if entry is None:
            raise UnverifiedRule(rule_key, self.key, "MISSING")
        if entry.status == RuleStatus.UNVERIFIED:
            raise UnverifiedRule(rule_key, self.key, entry.status.value)
        value = TypeAdapter(type_).validate_python(entry.value)
        return value, self.ref(rule_key)

    def optional(self, rule_key: str, type_: type[T] | Any) -> tuple[T | None, RuleRef | None]:
        """Like ``get``, but a rule absent from the profile means "not part of this system".

        An entry that is present but UNVERIFIED still raises: it is part of the system, just blocked.
        """
        if rule_key not in self.entries:
            return None, None
        return self.get(rule_key, type_)

    def ref(self, rule_key: str) -> RuleRef:
        entry = self.entries[rule_key]
        return RuleRef(
            profile_key=self.key,
            profile_version=self.version,
            rule_key=rule_key,
            status=entry.status,
            source=entry.source,
        )

    @property
    def needs_house_rule_acceptance(self) -> bool:
        """A campaign must explicitly accept any profile that contains house rules."""
        return self.requires_house_rule_acceptance or any(
            e.status == RuleStatus.HOUSE_RULE for e in self.entries.values())

    def content_hash(self) -> str:
        """Stable SHA-256 of everything that affects play. Campaigns pin to this."""
        canonical = self.model_dump(mode="json")
        return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def validate_entries(self) -> list[str]:
        """Check that every non-UNVERIFIED entry matches its declared type."""
        problems: list[str] = []
        for key, entry in self.entries.items():
            if key not in RULE_TYPES:
                problems.append(f"unknown rule key {key}")
                continue
            if entry.status == RuleStatus.UNVERIFIED:
                continue
            try:
                TypeAdapter(RULE_TYPES[key]).validate_python(entry.value)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                problems.append(f"{key}: {exc}")
        return problems

    # -- convenience accessors -------------------------------------------------

    def item(self, item_key: str) -> tuple[ItemDef | None, RuleRef]:
        catalog, ref = self.get("items.catalog", list[ItemDef])
        return next((i for i in catalog if i.key == item_key), None), ref

    def action_minutes(self, action: str) -> tuple[int, RuleRef]:
        table, ref = self.get("time.action_minutes", dict[str, int])
        if action not in table:
            raise UnverifiedRule(f"time.action_minutes.{action}", self.key, "MISSING")
        return table[action], ref


_PAGE_REF = re.compile(r"\bp(?:p|g)?\.?\s*\d+", re.IGNORECASE)
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def validate_profile(profile: RulesProfile) -> list[str]:
    """Every problem that makes a profile unusable. Empty list means valid.

    VERIFIED entries must cite a page and record when they were verified, so every
    verified value can be traced back to the owner's source.
    """
    problems = list(profile.validate_entries())
    if not _KEY.fullmatch(profile.key):
        problems.append(f"profile key {profile.key!r} must be lowercase letters, digits and hyphens")
    for key, entry in profile.entries.items():
        if entry.status == RuleStatus.VERIFIED:
            if not _PAGE_REF.search(entry.source):
                problems.append(f"{key}: VERIFIED entries must cite a page (e.g. 'Core Rulebook p.19')")
            if entry.verified_on is None:
                problems.append(f"{key}: VERIFIED entries need verified_on")
            if entry.value is None:
                problems.append(f"{key}: VERIFIED entries need a value")
    return problems


# Backwards-compatible name used by inventory code.
InventoryCapacity = CapacityRule
