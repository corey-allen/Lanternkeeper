"""Ability checks against profile-defined difficulty labels.

Callers (including the AI) may only pick a difficulty *label*. The number
behind it always comes from the active profile.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .character import ability_modifier
from .dice import RollRecord, Roller, roll_d20_with_mode
from .errors import ValidationFailed
from .profile import Ability, RulesProfile

RollMode = Literal["normal", "advantage", "disadvantage"]


class CheckRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    ability: Ability
    difficulty: str
    mode: RollMode = "normal"
    purpose: str = "ability check"


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    ability: Ability
    difficulty: str
    target: int
    modifier: int
    roll: RollRecord
    success: bool
    rule_refs: tuple[str, ...]


def resolve_check(
    request: CheckRequest,
    *,
    score: int,
    actor: str,
    profile: RulesProfile,
    roller: Roller,
) -> CheckResult:
    table, dc_ref = profile.get("check.difficulty", dict[str, int])
    if request.difficulty not in table:
        raise ValidationFailed(
            [f"unknown difficulty {request.difficulty!r}; allowed: {sorted(table)}"]
        )
    refs = [str(dc_ref)]
    if request.mode != "normal":
        enabled, adv_ref = profile.get("check.advantage_enabled", bool)
        refs.append(str(adv_ref))
        if not enabled:
            raise ValidationFailed(["advantage/disadvantage is disabled in this profile"])
    modifier, mod_ref = ability_modifier(score, profile)
    refs.append(str(mod_ref))
    target = table[request.difficulty]
    record = roll_d20_with_mode(
        roller, mode=request.mode, modifier=modifier, purpose=request.purpose,
        actor=actor, rule_refs=tuple(refs),
    )
    return CheckResult(
        ability=request.ability,
        difficulty=request.difficulty,
        target=target,
        modifier=modifier,
        roll=record,
        success=record.total >= target,
        rule_refs=tuple(refs),
    )
