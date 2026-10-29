"""Mechanic registry and coverage reporting.

Each mechanic lists the profile rules it needs. A mechanic is usable under a
profile only if it is implemented and every rule it needs is VERIFIED or an
accepted HOUSE_RULE. Unimplemented mechanics raise UnsupportedMechanic.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .errors import UnsupportedMechanic
from .profile import RulesProfile, RuleStatus


class CoverageStatus(StrEnum):
    AVAILABLE = "AVAILABLE"  # implemented and every needed rule is usable
    BLOCKED_UNVERIFIED = "BLOCKED_UNVERIFIED"  # implemented, but a needed rule is unverified
    UNSUPPORTED = "UNSUPPORTED"  # not implemented


class Mechanic(BaseModel):
    model_config = ConfigDict(frozen=True)
    key: str
    description: str
    implemented: bool
    requires: tuple[str, ...] = ()
    module: str = ""


MECHANICS: tuple[Mechanic, ...] = (
    Mechanic(key="dice.roll", description="Dice expressions and audited roll records",
             implemented=True, module="lk_rules.dice"),
    Mechanic(key="character.create", description="Character creation: stats, ancestry, class, HP, talents",
             implemented=True, module="lk_rules.creation",
             requires=("stats.generation", "ability.modifier_table", "character.classes",
                       "character.hit_points", "character.armor_class", "inventory.capacity")),
    Mechanic(key="character.modifiers", description="Typed modifiers from traits, features and talents",
             implemented=True, module="lk_rules.modifiers"),
    Mechanic(key="character.spells_known", description="Choosing known spells at creation and level-up",
             implemented=False),
    Mechanic(key="check.ability", description="Ability check vs difficulty label",
             implemented=True, module="lk_rules.checks",
             requires=("check.difficulty", "ability.modifier_table")),
    Mechanic(key="check.advantage", description="Advantage / disadvantage on checks",
             implemented=True, module="lk_rules.checks", requires=("check.advantage_enabled",)),
    Mechanic(key="inventory.manage", description="Add, remove, consume items with slot capacity",
             implemented=True, module="lk_rules.inventory",
             requires=("items.catalog", "inventory.capacity")),
    Mechanic(key="time.advance", description="Advance game clock by action cost",
             implemented=True, module="lk_rules.clock", requires=("time.action_minutes",)),
    Mechanic(key="light.duration", description="Light sources burn down with game time",
             implemented=True, module="lk_rules.clock", requires=("items.catalog",)),
    Mechanic(key="combat.initiative", description="Encounter start and turn order",
             implemented=True, module="lk_rules.combat", requires=("combat.initiative",)),
    Mechanic(key="combat.attack", description="Attack roll vs AC and damage",
             implemented=True, module="lk_rules.combat",
             requires=("combat.attack", "combat.zero_hp")),
    Mechanic(key="conditions.track", description="Track named conditions (no effects)",
             implemented=True, module="lk_rules.progression", requires=("conditions.catalog",)),
    Mechanic(key="quest.transition", description="Quest state machine",
             implemented=True, module="lk_rules.progression"),
    Mechanic(key="conditions.effects", description="Mechanical effects of conditions",
             implemented=False),
    Mechanic(key="combat.critical", description="Critical hits and fumbles", implemented=False),
    Mechanic(key="combat.morale", description="Creature morale", implemented=False),
    Mechanic(key="combat.dying", description="Dying, death, and stabilizing", implemented=False),
    Mechanic(key="rest.recover", description="Resting and recovery", implemented=False),
    Mechanic(key="advancement.level", description="XP and level advancement", implemented=False),
    Mechanic(key="spell.cast", description="Spellcasting", implemented=False),
    Mechanic(key="class.abilities", description="Class talents and features", implemented=False),
    Mechanic(key="ancestry", description="Ancestries and their traits", implemented=False),
    Mechanic(key="social.persuade", description="Social influence / reaction mechanics",
             implemented=False),
    Mechanic(key="encounter.random", description="Random encounter checks", implemented=False),
    Mechanic(key="treasure.generate", description="Treasure and loot generation", implemented=False),
)

_BY_KEY = {m.key: m for m in MECHANICS}


class CoverageRow(BaseModel):
    model_config = ConfigDict(frozen=True)
    mechanic: str
    description: str
    status: CoverageStatus
    blocking_rules: tuple[str, ...] = ()
    rule_statuses: dict[str, str] = {}


def coverage(profile: RulesProfile) -> list[CoverageRow]:
    rows: list[CoverageRow] = []
    for mech in MECHANICS:
        statuses = {
            r: (profile.entries[r].status.value if r in profile.entries else "MISSING")
            for r in mech.requires
        }
        blocking = tuple(r for r, s in statuses.items() if s in ("UNVERIFIED", "MISSING"))
        if not mech.implemented:
            status = CoverageStatus.UNSUPPORTED
        elif blocking:
            status = CoverageStatus.BLOCKED_UNVERIFIED
        else:
            status = CoverageStatus.AVAILABLE
        rows.append(CoverageRow(mechanic=mech.key, description=mech.description, status=status,
                                blocking_rules=blocking, rule_statuses=statuses))
    return rows


def require_supported(mechanic_key: str) -> Mechanic:
    mech = _BY_KEY.get(mechanic_key)
    if mech is None or not mech.implemented:
        raise UnsupportedMechanic(
            mechanic_key,
            "It is not implemented in the rules engine. Choose an explicit house-rule "
            "ruling outside the engine or wait for verified support.",
        )
    return mech


def uses_house_rules(profile: RulesProfile) -> bool:
    return any(e.status == RuleStatus.HOUSE_RULE for e in profile.entries.values())
