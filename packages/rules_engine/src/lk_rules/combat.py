"""Encounters, initiative, turn order, attacks, and damage."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .dice import DiceExpression, RollRecord, Roller, roll, roll_d20_with_mode
from .errors import CombatError
from .profile import AttackRule, InitiativeRule, RulesProfile, ZeroHpRule

Side = Literal["player", "enemy"]


class CombatantStatus(StrEnum):
    ACTIVE = "active"
    INCAPACITATED = "incapacitated"
    DEFEATED = "defeated"


class Combatant(BaseModel):
    model_config = ConfigDict(frozen=True)
    combatant_id: str
    name: str
    side: Side
    armor_class: int
    hp: int = Field(ge=0)
    max_hp: int = Field(ge=1)
    attack_modifier: int
    damage: str
    initiative_modifier: int
    status: CombatantStatus = CombatantStatus.ACTIVE
    initiative: int | None = None

    @property
    def can_act(self) -> bool:
        return self.status == CombatantStatus.ACTIVE


class EncounterStatus(StrEnum):
    ACTIVE = "active"
    PLAYER_VICTORY = "player_victory"
    PLAYER_DEFEAT = "player_defeat"


class Encounter(BaseModel):
    model_config = ConfigDict(frozen=True)
    round: int = 1
    turn_index: int = 0
    combatants: tuple[Combatant, ...]
    status: EncounterStatus = EncounterStatus.ACTIVE

    def get(self, combatant_id: str) -> Combatant:
        for c in self.combatants:
            if c.combatant_id == combatant_id:
                return c
        raise CombatError(f"No combatant {combatant_id!r}", combatant_id=combatant_id)

    @property
    def current(self) -> Combatant:
        return self.combatants[self.turn_index]

    def replace(self, combatant: Combatant) -> Encounter:
        combatants = tuple(
            combatant if c.combatant_id == combatant.combatant_id else c for c in self.combatants
        )
        return self.model_copy(update={"combatants": combatants})


class InitiativeOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)
    encounter: Encounter
    rolls: tuple[RollRecord, ...]
    rule_refs: tuple[str, ...]


def start_encounter(
    combatants: list[Combatant], *, profile: RulesProfile, roller: Roller
) -> InitiativeOutcome:
    if not any(c.side == "player" for c in combatants) or not any(c.side == "enemy" for c in combatants):
        raise CombatError("An encounter needs at least one player and one enemy combatant")
    if len({c.combatant_id for c in combatants}) != len(combatants):
        raise CombatError("Duplicate combatant ids")
    _, ref = profile.get("combat.initiative", InitiativeRule)
    rolls: list[RollRecord] = []
    rolled: list[Combatant] = []
    for c in combatants:
        record = roll_d20_with_mode(
            roller, mode="normal", modifier=c.initiative_modifier,
            purpose="initiative", actor=c.combatant_id, rule_refs=(str(ref),),
        )
        rolls.append(record)
        rolled.append(c.model_copy(update={"initiative": record.total}))
    # Highest initiative first; ties broken by player first, then stable input order.
    order = sorted(
        enumerate(rolled),
        key=lambda pair: (-(pair[1].initiative or 0), 0 if pair[1].side == "player" else 1, pair[0]),
    )
    encounter = Encounter(combatants=tuple(c for _, c in order))
    return InitiativeOutcome(encounter=encounter, rolls=tuple(rolls), rule_refs=(str(ref),))


class AttackOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)
    attacker_id: str
    target_id: str
    attack_roll: RollRecord
    target_armor_class: int
    hit: bool
    damage_roll: RollRecord | None
    damage: int
    target_hp_before: int
    target_hp_after: int
    target_status: CombatantStatus
    encounter: Encounter
    rule_refs: tuple[str, ...]


def apply_damage(target: Combatant, amount: int, *, profile: RulesProfile) -> tuple[Combatant, str]:
    if amount < 0:
        raise CombatError("Damage cannot be negative", amount=amount)
    rule, ref = profile.get("combat.zero_hp", ZeroHpRule)
    hp = max(0, target.hp - amount)
    status = target.status
    if hp == 0:
        outcome = rule.character_outcome if target.side == "player" else rule.creature_outcome
        status = CombatantStatus(outcome)
    return target.model_copy(update={"hp": hp, "status": status}), str(ref)


def resolve_attack(
    encounter: Encounter, *, attacker_id: str, target_id: str, profile: RulesProfile, roller: Roller
) -> AttackOutcome:
    if encounter.status != EncounterStatus.ACTIVE:
        raise CombatError("Encounter is over")
    attacker = encounter.get(attacker_id)
    target = encounter.get(target_id)
    if encounter.current.combatant_id != attacker_id:
        raise CombatError(f"It is not {attacker.name}'s turn", current=encounter.current.combatant_id)
    if not attacker.can_act:
        raise CombatError(f"{attacker.name} cannot act")
    if attacker.side == target.side:
        raise CombatError("Cannot attack an ally")
    if not target.can_act:
        raise CombatError(f"{target.name} is already out of the fight")
    rule, attack_ref = profile.get("combat.attack", AttackRule)
    refs = [str(attack_ref)]
    attack_roll = roll_d20_with_mode(
        roller, mode="normal", modifier=attacker.attack_modifier, purpose="attack",
        actor=attacker_id, rule_refs=(str(attack_ref),),
    )
    hit = attack_roll.total >= target.armor_class
    damage_roll = None
    damage = 0
    updated_target = target
    if hit:
        damage_roll = roll(DiceExpression.parse(attacker.damage), roller, purpose="damage",
                           actor=attacker_id, rule_refs=(str(attack_ref),))
        damage = max(rule.minimum_damage, damage_roll.total)
        updated_target, zero_ref = apply_damage(target, damage, profile=profile)
        refs.append(zero_ref)
    updated = encounter.replace(updated_target)
    updated = _update_status(updated)
    return AttackOutcome(
        attacker_id=attacker_id,
        target_id=target_id,
        attack_roll=attack_roll,
        target_armor_class=target.armor_class,
        hit=hit,
        damage_roll=damage_roll,
        damage=damage,
        target_hp_before=target.hp,
        target_hp_after=updated_target.hp,
        target_status=updated_target.status,
        encounter=updated,
        rule_refs=tuple(refs),
    )


def _update_status(encounter: Encounter) -> Encounter:
    players_up = any(c.side == "player" and c.can_act for c in encounter.combatants)
    enemies_up = any(c.side == "enemy" and c.can_act for c in encounter.combatants)
    if not enemies_up:
        return encounter.model_copy(update={"status": EncounterStatus.PLAYER_VICTORY})
    if not players_up:
        return encounter.model_copy(update={"status": EncounterStatus.PLAYER_DEFEAT})
    return encounter


def next_turn(encounter: Encounter) -> Encounter:
    """Advance to the next combatant able to act, incrementing the round on wrap-around."""
    if encounter.status != EncounterStatus.ACTIVE:
        return encounter
    n = len(encounter.combatants)
    index, rnd = encounter.turn_index, encounter.round
    for _ in range(n):
        index += 1
        if index >= n:
            index, rnd = 0, rnd + 1
        if encounter.combatants[index].can_act:
            return encounter.model_copy(update={"turn_index": index, "round": rnd})
    raise CombatError("No combatant can act")
