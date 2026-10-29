"""Ability checks against profile-defined difficulty labels.

Callers (including the AI) may only pick a difficulty *label* and a task category. The number
behind the label, the character's modifiers, the natural-roll rules and any automatic success all
come from the profile and the character's recorded features.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .character import ability_modifier
from .definitions import NamedEntry, NaturalResults
from .dice import RollRecord, Roller, roll_d20_with_mode
from .errors import ValidationFailed
from .modifiers import Applied, Feature, FeatureKind, Modifier, ModTarget, Scope, collect
from .profile import Ability, RulesProfile

RollMode = Literal["normal", "advantage", "disadvantage"]


class CheckRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    ability: Ability
    difficulty: str
    mode: RollMode = "normal"
    purpose: str = "ability check"
    task_tag: str | None = None
    conditions: tuple[str, ...] = ()  # situational context, e.g. "total_darkness"


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    ability: Ability
    difficulty: str
    target: int
    modifier: int  # stat modifier plus modifier bonuses
    roll: RollRecord | None  # None when the task succeeded automatically
    success: bool
    automatic: bool = False
    natural: Literal["max", "min"] | None = None
    applied: tuple[Applied, ...] = ()
    rule_refs: tuple[str, ...]


def task_tags(profile: RulesProfile) -> list[str]:
    tags, _ = profile.optional("check.task_tags", list[NamedEntry])
    return [t.key for t in tags or []]


def combine_modes(*modes: RollMode) -> RollMode:
    adv, dis = "advantage" in modes, "disadvantage" in modes
    if adv and not dis:
        return "advantage"
    if dis and not adv:
        return "disadvantage"
    return "normal"


def trained_auto_success(features: tuple[Feature, ...] | list[Feature], task_tag: str | None, *,
                         under_pressure: bool) -> Feature | None:
    """The feature that makes this task succeed without a roll, if any (ADR-0009 #3)."""
    if task_tag is None or under_pressure:
        return None
    return next((f for f in features if f.kind == FeatureKind.TRAINED_TASKS and task_tag in f.tags), None)


def resolve_check(
    request: CheckRequest,
    *,
    score: int,
    actor: str,
    profile: RulesProfile,
    roller: Roller,
    scores: dict[str, int] | None = None,
    modifiers: tuple[Modifier, ...] | list[Modifier] = (),
    level: int = 1,
) -> CheckResult:
    table, dc_ref = profile.get("check.difficulty", dict[str, int])
    if request.difficulty not in table:
        raise ValidationFailed([f"unknown difficulty {request.difficulty!r}; allowed: {sorted(table)}"])
    if request.task_tag is not None and request.task_tag not in task_tags(profile):
        raise ValidationFailed([f"unknown task category {request.task_tag!r}"])
    refs = [str(dc_ref)]
    stat_mod, mod_ref = ability_modifier(score, profile)
    refs.append(str(mod_ref))
    all_scores = dict(scores or {})
    all_scores[request.ability.value] = score
    stat_mods = {a: ability_modifier(all_scores.get(a.value, 10), profile)[0] for a in Ability}

    contexts = [Scope(stat=request.ability)]
    if request.task_tag:
        contexts.append(Scope(stat=request.ability, check_tag=request.task_tag))
        contexts.append(Scope(check_tag=request.task_tag))
    contexts += [Scope(condition=c) for c in request.conditions]
    bonus, adv, dis, applied = 0, False, False, []
    seen: set[int] = set()
    for ctx in contexts:
        for i, mod in enumerate(modifiers):
            if i in seen or mod.target != ModTarget.STAT_CHECK or not mod.scope.matches(ctx):
                continue
            seen.add(i)
            got = collect([mod], ModTarget.STAT_CHECK, ctx, stat_mods=stat_mods, level=level)
            bonus += got.bonus
            adv, dis = adv or got.advantage, dis or got.disadvantage
            applied.extend(got.applied)
    mode = combine_modes(request.mode, "advantage" if adv else "normal", "disadvantage" if dis else "normal")
    if mode != "normal" or request.mode != "normal":
        enabled, adv_ref = profile.get("check.advantage_enabled", bool)
        refs.append(str(adv_ref))
        if not enabled:
            raise ValidationFailed(["advantage/disadvantage is disabled in this profile"])

    total_mod = stat_mod + bonus
    target = table[request.difficulty]
    record = roll_d20_with_mode(roller, mode=mode, modifier=total_mod, purpose=request.purpose, actor=actor,
                                rule_refs=tuple(refs))
    success = record.total >= target
    natural = None
    naturals, nat_ref = profile.optional("check.natural_results", NaturalResults)
    if naturals is not None:
        face = record.natural
        if face == 20 and naturals.max_face == "auto_success":
            natural, success = "max", True
        elif face == 1 and naturals.min_face == "auto_failure":
            natural, success = "min", False
        if natural:
            refs.append(str(nat_ref))
    return CheckResult(ability=request.ability, difficulty=request.difficulty, target=target, modifier=total_mod,
                       roll=record, success=success, natural=natural, applied=tuple(applied),
                       rule_refs=tuple(dict.fromkeys(refs)))


def automatic_success(request: CheckRequest, feature: Feature, profile: RulesProfile) -> CheckResult:
    table, dc_ref = profile.get("check.difficulty", dict[str, int])
    return CheckResult(ability=request.ability, difficulty=request.difficulty, target=table[request.difficulty],
                       modifier=0, roll=None, success=True, automatic=True,
                       applied=(Applied(source=feature.source, label=feature.name, amount=0),),
                       rule_refs=(str(dc_ref),))


__all__ = ["CheckRequest", "CheckResult", "NaturalResults", "automatic_success", "combine_modes",
           "resolve_check", "task_tags", "trained_auto_success"]
