"""Character-building definitions held in a rules profile: ancestries, classes, talent tables,
stat generation, hit points, languages, alignments, deities, backgrounds and titles.

Options are templates. A template may leave a parameter open (a stat, weapon type, armor type,
spell, or which alternative to take); the player resolves it during creation and the result is a
concrete list of ``Modifier`` and ``Feature`` objects with a recorded source.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .abilities import Ability
from .modifiers import Feature, ModMode, ModTarget

ChoiceParam = Literal["stat", "weapon_type", "armor_type", "spell", "attack_kind"]


class ChoiceSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    param: ChoiceParam
    allowed: tuple[str, ...] | None = None  # None = any value valid for the param
    prompt: str = ""


class ModifierTemplate(BaseModel):
    """Like ``Modifier``; string fields may be ``$stat``/``$weapon_type``/... placeholders."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    target: ModTarget
    mode: ModMode = ModMode.BONUS
    amount: int = 0
    amount_from_stat: str | None = None
    positive_only: bool = False
    per_level_divisor: int | None = Field(default=None, ge=1)
    scope: dict[str, str] = Field(default_factory=dict)
    label: str = ""


class OptionDef(BaseModel):
    """One ancestry trait, class feature, or talent table result."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str = Field(min_length=1, max_length=64)
    label: str
    kind: Literal["grant", "alternatives", "choose_row", "distribute_stat_points", "deferred", "unsupported"] = "grant"
    modifiers: tuple[ModifierTemplate, ...] = ()
    features: tuple[Feature, ...] = ()
    choices: tuple[ChoiceSpec, ...] = ()
    alternatives: tuple[OptionDef, ...] = ()  # kind == "alternatives": the player takes exactly one
    points: int | None = Field(default=None, ge=1)  # kind == "distribute_stat_points"
    reason: str = ""  # kind == "deferred"/"unsupported": what is missing
    text_ref: str = ""  # page reference, never rules text


class TalentRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    min: int
    max: int
    option: OptionDef


class TalentTable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dice: str
    rows: tuple[TalentRow, ...]

    def row_for(self, total: int) -> TalentRow:
        for row in self.rows:
            if row.min <= total <= row.max:
                return row
        raise ValueError(f"talent table has no row for {total}")


class LanguageGrant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    fixed: tuple[str, ...] = ()
    choose_from: tuple[str, ...] = ()  # pick choose_from_count of these
    choose_from_count: int = 0
    choose_common: int = 0
    choose_rare: int = 0


class SpellcastingDef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tradition: str
    stat: Ability
    spells_known: dict[int, tuple[int, ...]]  # level -> spells known per tier (index 0 = tier 1)


class ClassDef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    name: str
    hit_die: str
    weapons: tuple[str, ...] = ("all",)
    armor: tuple[str, ...] = ("all",)
    shields: bool = True
    features: tuple[OptionDef, ...] = ()
    talent_table: TalentTable | None = None
    talent_rolls_level_1: int = Field(default=0, ge=0)
    spellcasting: SpellcastingDef | None = None
    languages: LanguageGrant = LanguageGrant()
    requires_deity: bool = False
    text_ref: str = ""


class AncestryDef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    name: str
    languages: LanguageGrant = LanguageGrant()
    traits: tuple[OptionDef, ...] = ()
    text_ref: str = ""


class StatGeneration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    method: Literal["manual", "roll_in_order"]
    dice: str = "3d6"
    reroll_if_none_at_least: int | None = None  # a whole new set is allowed if no score reaches this
    rerolls_allowed: int = Field(default=0, ge=0)


class HitPointRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    level_1: Literal["roll_hit_die", "max_hit_die"]
    add_stat: Ability = Ability.CON
    minimum: int = Field(default=1, ge=0)


class LanguagesDef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    common: tuple[str, ...]
    rare: tuple[str, ...] = ()


class DeityDef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    name: str
    alignment: str


class NamedEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    name: str


class TitleRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    class_key: str
    alignment: str
    min_level: int
    max_level: int
    title: str


class DiceAmount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dice: str
    multiplier: int = 1
    unit: str = "gp"


class CapacityRule(BaseModel):
    """Gear slot capacity: a fixed number, or a stat score with a floor."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    method: Literal["fixed_slots", "stat_or_floor"]
    slots: int | None = Field(default=None, ge=1)
    stat: Ability | None = None
    floor: int | None = Field(default=None, ge=0)


class NaturalResults(BaseModel):
    """What a natural maximum or minimum on the d20 means for a check."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    max_face: Literal["auto_success", "none"]
    min_face: Literal["auto_failure", "none"]


# ---- gear ----------------------------------------------------------------------------

Range = Literal["close", "near", "far"]
WeaponProperty = Literal["finesse", "loading", "thrown", "two_handed", "versatile"]


class Cost(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    amount: int = Field(ge=0)
    unit: str  # a currency denomination key


class WeaponDef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["melee", "ranged", "both"] = "melee"
    ranges: tuple[Range, ...] = ("close",)
    damage: str
    damage_two_handed: str | None = None  # versatile weapons
    properties: tuple[WeaponProperty, ...] = ()
    attack_ability: Ability | None = None  # explicit override; otherwise combat.attack_stats decides


class ArmorDef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["body", "shield"]
    base_ac: int | None = None  # body armor: replaces the unarmored base
    add_dex: bool = False
    ac_bonus: int = 0  # shields
    stealth: Literal["normal", "disadvantage"] = "normal"
    swim: Literal["normal", "disadvantage", "none"] = "normal"
    occupies_hand: bool = False


class LightDef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    duration_minutes: int = Field(ge=1)
    radius: Literal["close", "near", "double_near"] | None = None
    fuel_item_key: str | None = None  # lighting consumes one of these instead of the item itself


TimingMode = Literal["real_time", "rounds"]
MultipleLights = Literal["ride_along", "restart"]


class LightTimingRule(BaseModel):
    """How light (and the play clock) is timed. The first listed mode and option are the defaults."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    modes: list[TimingMode] = Field(min_length=1)
    rounds_per_hour: int = Field(ge=1, le=60)
    multiple_sources: list[MultipleLights] = Field(min_length=1)

    @model_validator(mode="after")
    def _whole_minutes(self) -> LightTimingRule:
        if 60 % self.rounds_per_hour:
            raise ValueError("rounds_per_hour must divide 60 so a round is a whole number of minutes")
        return self

    @property
    def minutes_per_round(self) -> int:
        return 60 // self.rounds_per_hour


class DarknessRule(BaseModel):
    """Effect of total darkness on a creature that is not darkness-adapted."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    sight_checks: Literal["disadvantage"]


class KitItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    item_key: str
    quantity: int = Field(ge=1)


class ItemDef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    name: str
    category: Literal["gear", "weapon", "armor", "shield", "ammunition", "light", "kit", "container"] = "gear"
    cost: Cost | None = None
    slots: int = Field(default=1, ge=0)  # gear slots per full "slot unit"
    quantity_per_slot: int = Field(default=1, ge=1)  # how many fit in one slot unit
    free_quantity: int = Field(default=0, ge=0)  # carried without slots (e.g. the first backpack)
    weapon: WeaponDef | None = None
    armor: ArmorDef | None = None
    light: LightDef | None = None
    consumable: bool = False
    contents: tuple[KitItem, ...] = ()  # kits expand into their contents when acquired


class Denomination(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    name: str
    value: int = Field(ge=1)  # in the smallest denomination


class CurrencyDef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    denominations: tuple[Denomination, ...]
    coins_per_slot: int = Field(ge=1)
    free_coins: int = Field(default=0, ge=0)


class AttackStatsRule(BaseModel):
    """Which stat an attack uses. Finesse and thrown weapons may use either listed stat."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    melee: Ability
    ranged: Ability
    either: tuple[Ability, ...] = ()  # for finesse/thrown: the wielder uses the better of these
