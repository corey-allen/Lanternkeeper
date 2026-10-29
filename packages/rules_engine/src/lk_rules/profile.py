"""Rules profiles: every numeric rule value lives here, with provenance and status.

The engine never hard-codes game-system values. A mechanic asks the active
profile for the rule it needs; if the rule is missing or UNVERIFIED, the
mechanic fails with :class:`UnverifiedRule` instead of guessing.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

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


class Ability(StrEnum):
    STR = "STR"
    DEX = "DEX"
    CON = "CON"
    INT = "INT"
    WIS = "WIS"
    CHA = "CHA"


# ---- typed rule values -------------------------------------------------------


class ScoreRange(BaseModel):
    min: int
    max: int


class ModifierBand(BaseModel):
    min: int
    max: int
    modifier: int


class ClassDef(BaseModel):
    key: str
    name: str
    base_hit_points: int = Field(ge=1)


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


class InventoryCapacity(BaseModel):
    method: Literal["fixed_slots"]
    slots: int = Field(ge=1)


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
    "character.classes": list[ClassDef],
    "character.armor_class": ArmorClassRule,
    "items.catalog": list[ItemDef],
    "inventory.capacity": InventoryCapacity,
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

    def ref(self, rule_key: str) -> RuleRef:
        entry = self.entries[rule_key]
        return RuleRef(
            profile_key=self.key,
            profile_version=self.version,
            rule_key=rule_key,
            status=entry.status,
            source=entry.source,
        )

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
