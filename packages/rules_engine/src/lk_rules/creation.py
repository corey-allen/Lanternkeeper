"""Character creation as a server-rolled draft.

Stages: ``stats`` -> ``origin`` -> ``rolled`` -> finalize. Stats (when the profile says they are
rolled), hit points, talent results and starting gold are rolled by the server and recorded;
the player cannot reroll except where the profile allows it. Player choices (ancestry, class, trait
and talent parameters, languages, alignment, deity) are validated together at finalize, and every
problem is reported at once.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .abilities import Ability
from .definitions import (
    AncestryDef,
    ClassDef,
    DeityDef,
    DiceAmount,
    HitPointRule,
    LanguageGrant,
    LanguagesDef,
    NamedEntry,
    OptionDef,
    StatGeneration,
    TalentTable,
    TitleRow,
)
from .dice import DiceExpression, RollRecord, Roller, roll, roll_die_with_mode
from .errors import ValidationFailed
from .inventory import capacity_slots
from .modifiers import Feature, Modifier, ModTarget, Scope, collect
from .profile import ArmorClassRule, ModifierBand, RuleRef, RulesProfile, ScoreRange

ABILITIES = tuple(Ability)


# ---- option resolution -----------------------------------------------------------


class Resolved(BaseModel):
    model_config = ConfigDict(frozen=True)
    modifiers: tuple[Modifier, ...] = ()
    features: tuple[Feature, ...] = ()
    pending: tuple[str, ...] = ()


def _sub(value: Any, choices: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return choices.get(value[1:])
    return value


def resolve_option(option: OptionDef, choices: dict[str, Any], *, source: str, issues: list[str],
                   table: TalentTable | None = None, _depth: int = 0) -> Resolved:
    where = f"{option.label} ({source})"
    if option.kind == "deferred":
        return Resolved(pending=(f"{where}: {option.reason or 'deferred until supported'}",))
    if option.kind == "unsupported":
        return Resolved(pending=(f"{where}: not supported ({option.reason})",))
    if option.kind == "alternatives":
        idx = choices.get("alternative")
        if not isinstance(idx, int) or not 0 <= idx < len(option.alternatives):
            issues.append(f"{where}: choose one of {[a.label for a in option.alternatives]}")
            return Resolved()
        return resolve_option(option.alternatives[idx], choices, source=source, issues=issues, table=table,
                              _depth=_depth + 1)
    if option.kind == "choose_row":
        row = choices.get("row")
        if table is None or not isinstance(row, int) or not 0 <= row < len(table.rows):
            issues.append(f"{where}: choose a row of the talent table")
            return Resolved()
        chosen = table.rows[row].option
        if _depth > 2 or _contains_choose_row(chosen):
            issues.append(f"{where}: that row cannot be chosen this way")
            return Resolved()
        return resolve_option(chosen, choices.get("row_choices") or {}, source=f"{source}->{chosen.key}",
                              issues=issues, table=table, _depth=_depth + 1)
    if option.kind == "distribute_stat_points":
        dist = choices.get("distribute") or {}
        try:
            parsed = {Ability(k): int(v) for k, v in dist.items()}
        except (ValueError, TypeError):
            parsed = None
        if parsed is None or any(v < 0 for v in parsed.values()) or sum(parsed.values()) != option.points:
            issues.append(f"{where}: distribute exactly {option.points} points among stats")
            return Resolved()
        return Resolved(modifiers=tuple(
            Modifier(target=ModTarget.STAT_SCORE, amount=v, scope=Scope(stat=a), source=source, label=option.label)
            for a, v in parsed.items() if v))

    pending: list[str] = []
    missing_spell = False
    for spec in option.choices:
        value = choices.get(spec.param)
        if value is None:
            if spec.param == "spell":
                missing_spell = True
                pending.append(f"{where}: choose a spell once spellcasting is supported")
                continue
            issues.append(f"{where}: choose {spec.param}" + (f" from {list(spec.allowed)}" if spec.allowed else ""))
            continue
        if spec.allowed is not None and value not in spec.allowed:
            issues.append(f"{where}: {spec.param} must be one of {list(spec.allowed)}")
    modifiers: list[Modifier] = []
    for template in option.modifiers:
        scope = {k: _sub(v, choices) for k, v in template.scope.items()}
        if missing_spell and "spell" in scope and scope["spell"] is None:
            continue
        if any(v is None for v in scope.values()):
            continue  # the missing choice is already reported
        try:
            modifiers.append(Modifier(
                target=template.target, mode=template.mode, amount=template.amount,
                amount_from_stat=_sub(template.amount_from_stat, choices), positive_only=template.positive_only,
                per_level_divisor=template.per_level_divisor, scope=Scope(**scope), source=source,
                label=template.label or option.label))
        except ValidationError:
            issues.append(f"{where}: invalid choice")
    features = tuple(f.model_copy(update={"source": source}) for f in option.features)
    return Resolved(modifiers=tuple(modifiers), features=features, pending=tuple(pending))


def _contains_choose_row(option: OptionDef) -> bool:
    return option.kind == "choose_row" or any(_contains_choose_row(a) for a in option.alternatives)


# ---- draft ---------------------------------------------------------------------------


class TalentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roll_id: str
    total: int
    row_index: int
    option_key: str
    choices: dict[str, Any] = Field(default_factory=dict)


class CreationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["stats", "origin", "rolled"]
    stat_method: Literal["manual", "roll_in_order"]
    base_scores: dict[str, int] | None = None
    stat_roll_ids: list[str] = Field(default_factory=list)
    rerolls_used: int = 0
    ancestry_key: str | None = None
    class_key: str | None = None
    trait_choices: dict[str, dict[str, Any]] = Field(default_factory=dict)
    hp_roll_total: int | None = None
    hp_roll_id: str | None = None
    talents: list[TalentResult] = Field(default_factory=list)
    gold_amount: int | None = None
    gold_unit: str | None = None
    gold_roll_id: str | None = None
    roll_basis: list[str] = Field(default_factory=list)  # roll-affecting modifiers when the dice were rolled
    name: str = ""
    alignment: str | None = None
    background: str | None = None
    deity: str | None = None
    languages: list[str] = Field(default_factory=list)


def _stat_rule(profile: RulesProfile) -> StatGeneration:
    rule, _ = profile.optional("stats.generation", StatGeneration)
    return rule or StatGeneration(method="manual")


def _roll_stats(profile: RulesProfile, roller: Roller, rule: StatGeneration) -> tuple[dict[str, int], list[RollRecord]]:
    expr = DiceExpression.parse(rule.dice)
    ref = str(profile.ref("stats.generation"))
    rolls = [roll(expr, roller, purpose=f"creation:stat:{a.value}", actor="character", rule_refs=(ref,))
             for a in ABILITIES]
    return {a.value: r.total for a, r in zip(ABILITIES, rolls, strict=True)}, rolls


def start_draft(profile: RulesProfile, roller: Roller) -> tuple[CreationDraft, list[RollRecord]]:
    rule = _stat_rule(profile)
    if rule.method == "manual":
        return CreationDraft(stage="stats", stat_method="manual"), []
    scores, rolls = _roll_stats(profile, roller, rule)
    return CreationDraft(stage="origin", stat_method="roll_in_order", base_scores=scores,
                         stat_roll_ids=[r.roll_id for r in rolls]), rolls


def can_reroll_stats(draft: CreationDraft, profile: RulesProfile) -> bool:
    rule = _stat_rule(profile)
    return (rule.method == "roll_in_order" and draft.stage == "origin" and draft.base_scores is not None
            and rule.reroll_if_none_at_least is not None and draft.rerolls_used < rule.rerolls_allowed
            and max(draft.base_scores.values()) < rule.reroll_if_none_at_least)


def reroll_stats(draft: CreationDraft, profile: RulesProfile, roller: Roller) -> tuple[CreationDraft, list[RollRecord]]:
    if not can_reroll_stats(draft, profile):
        raise ValidationFailed(["a new set of stats is not allowed for these scores"])
    scores, rolls = _roll_stats(profile, roller, _stat_rule(profile))
    return draft.model_copy(update={"base_scores": scores, "stat_roll_ids": [r.roll_id for r in rolls],
                                    "rerolls_used": draft.rerolls_used + 1}), rolls


def set_manual_scores(draft: CreationDraft, scores: dict[str, int], profile: RulesProfile) -> CreationDraft:
    if draft.stat_method != "manual" or draft.stage == "rolled":
        raise ValidationFailed(["this profile rolls stats; they cannot be entered"])
    rng, _ = profile.get("ability.score_range", ScoreRange)
    issues = [f"{a.value} missing" for a in ABILITIES if a.value not in scores]
    issues += [f"{k} {v} outside allowed range {rng.min}-{rng.max}" for k, v in scores.items()
               if k in {a.value for a in ABILITIES} and not rng.min <= v <= rng.max]
    if issues:
        raise ValidationFailed(issues)
    return draft.model_copy(update={"base_scores": {a.value: scores[a.value] for a in ABILITIES}, "stage": "origin"})


def _ancestries(profile: RulesProfile) -> list[AncestryDef]:
    value, _ = profile.optional("character.ancestries", list[AncestryDef])
    return value or []


def _classes(profile: RulesProfile) -> list[ClassDef]:
    value, _ = profile.get("character.classes", list[ClassDef])
    return value


def choose_origin(draft: CreationDraft, profile: RulesProfile, *, ancestry_key: str | None,
                  class_key: str) -> CreationDraft:
    if draft.stage != "origin":
        raise ValidationFailed(["ancestry and class are chosen after stats and before rolling"])
    issues = []
    ancestries = _ancestries(profile)
    if ancestries and ancestry_key not in {a.key for a in ancestries}:
        issues.append(f"choose an ancestry from {[a.key for a in ancestries]}")
    if class_key not in {c.key for c in _classes(profile)}:
        issues.append(f"choose a class from {[c.key for c in _classes(profile)]}")
    if issues:
        raise ValidationFailed(issues)
    return draft.model_copy(update={"ancestry_key": ancestry_key if ancestries else None, "class_key": class_key})


def _origin_resolved(draft: CreationDraft, profile: RulesProfile, issues: list[str]) -> list[Resolved]:
    out = []
    ancestry = next((a for a in _ancestries(profile) if a.key == draft.ancestry_key), None)
    klass = next(c for c in _classes(profile) if c.key == draft.class_key)
    for owner, options in ((f"ancestry:{ancestry.key}" if ancestry else "", ancestry.traits if ancestry else ()),
                           (f"class:{klass.key}", klass.features)):
        for opt in options:
            out.append(resolve_option(opt, draft.trait_choices.get(opt.key, {}), source=f"{owner}/{opt.key}",
                                      issues=issues))
    return out


_ROLL_TARGETS = (ModTarget.HIT_POINT_ROLL, ModTarget.TALENT_ROLLS_LEVEL_1)


def _roll_basis(modifiers: list[Modifier] | tuple[Modifier, ...]) -> list[str]:
    return sorted(f"{m.source}:{m.target.value}:{m.mode.value}:{m.amount}" for m in modifiers
                  if m.target in _ROLL_TARGETS)


def roll_class(draft: CreationDraft, profile: RulesProfile, roller: Roller) -> tuple[CreationDraft, list[RollRecord]]:
    """Roll hit points, level-1 talents and starting gold. Locks ancestry and class."""
    if draft.stage != "origin" or draft.class_key is None:
        raise ValidationFailed(["choose ancestry and class first"])
    klass = next(c for c in _classes(profile) if c.key == draft.class_key)
    origin = [m for r in _origin_resolved(draft, profile, []) for m in r.modifiers]
    mods = _stat_mods(draft.base_scores or {}, profile)
    rolls: list[RollRecord] = []
    hp_rule, hp_ref = profile.get("character.hit_points", HitPointRule)
    die = DiceExpression.parse(klass.hit_die).terms[0].sides
    update: dict[str, Any] = {"stage": "rolled", "roll_basis": _roll_basis(origin)}
    if hp_rule.level_1 == "roll_hit_die":
        mode = collect(origin, ModTarget.HIT_POINT_ROLL, Scope(), stat_mods=mods, level=1).roll_mode
        rec = roll_die_with_mode(roller, sides=die, mode=mode, modifier=0, purpose="creation:hit_points",
                                 actor="character", rule_refs=(str(hp_ref),))
        rolls.append(rec)
        update.update(hp_roll_total=rec.total, hp_roll_id=rec.roll_id)
    else:
        update.update(hp_roll_total=die, hp_roll_id=None)
    count = klass.talent_rolls_level_1 + collect(origin, ModTarget.TALENT_ROLLS_LEVEL_1, Scope(),
                                                 stat_mods=mods, level=1).bonus
    talents = []
    if klass.talent_table and count:
        for _ in range(count):
            rec = roll(klass.talent_table.dice, roller, purpose=f"creation:talent:{klass.key}", actor="character",
                       rule_refs=(str(profile.ref("character.classes")),))
            rolls.append(rec)
            row = klass.talent_table.row_for(rec.total)
            talents.append(TalentResult(roll_id=rec.roll_id, total=rec.total,
                                        row_index=klass.talent_table.rows.index(row), option_key=row.option.key))
    update["talents"] = talents
    gold, gold_ref = profile.optional("character.starting_gold", DiceAmount)
    if gold is not None:
        rec = roll(gold.dice, roller, purpose="creation:starting_gold", actor="character", rule_refs=(str(gold_ref),))
        rolls.append(rec)
        update.update(gold_amount=rec.total * gold.multiplier, gold_unit=gold.unit, gold_roll_id=rec.roll_id)
    return draft.model_copy(update=update), rolls


# ---- finalize ----------------------------------------------------------------------


class CharacterBuild(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    class_key: str
    class_name: str
    ancestry_key: str | None
    ancestry_name: str | None
    level: int
    base_scores: dict[str, int]
    scores: dict[str, int]
    stat_modifiers: dict[str, int]
    max_hp: int
    armor_class: int
    gear_slots: int
    languages: tuple[str, ...]
    alignment: str | None
    deity: str | None
    background: str | None
    title: str | None
    modifiers: tuple[Modifier, ...]
    features: tuple[Feature, ...]
    pending: tuple[str, ...]
    gold_amount: int | None
    gold_unit: str | None
    rule_refs: tuple[str, ...]


def _stat_mods(scores: dict[str, int], profile: RulesProfile) -> dict[Ability, int]:
    table, _ = profile.get("ability.modifier_table", list[ModifierBand])
    out = {}
    for a in ABILITIES:
        value = scores.get(a.value, 10)
        band = next((b for b in table if b.min <= value <= b.max), None)
        if band is None:
            raise ValidationFailed([f"no modifier defined for {a.value} {value}"])
        out[a] = band.modifier
    return out


def _check_languages(chosen: list[str], grants: list[LanguageGrant], langs: LanguagesDef | None,
                     known: set[str], issues: list[str]) -> None:
    if len(set(chosen)) != len(chosen):
        issues.append("languages: no duplicates")
    pool = [x for x in chosen if x not in known]
    if len(pool) != len(chosen):
        issues.append("languages: you already know some of the chosen languages")
    common = set(langs.common) if langs else set()
    rare = set(langs.rare) if langs else set()
    for g in grants:
        for _ in range(g.choose_from_count):
            pick = next((x for x in pool if x in g.choose_from), None)
            if pick is None:
                issues.append(f"languages: choose one of {list(g.choose_from)}")
            else:
                pool.remove(pick)
    for count, allowed, label in ((sum(g.choose_rare for g in grants), rare, "rare"),
                                  (sum(g.choose_common for g in grants), common, "common")):
        for _ in range(count):
            pick = next((x for x in pool if x in allowed), None)
            if pick is None:
                issues.append(f"languages: choose {count} {label} language(s)")
                break
            pool.remove(pick)
    if pool:
        issues.append(f"languages: {pool} are not granted by your ancestry or class")


def finalize(draft: CreationDraft, profile: RulesProfile) -> CharacterBuild:
    if draft.stage != "rolled" or draft.base_scores is None or draft.class_key is None:
        raise ValidationFailed(["roll hit points and talents before finishing the character"])
    issues: list[str] = []
    refs: list[RuleRef] = [profile.ref("character.classes"), profile.ref("ability.modifier_table")]
    klass = next(c for c in _classes(profile) if c.key == draft.class_key)
    ancestry = next((a for a in _ancestries(profile) if a.key == draft.ancestry_key), None)
    if not draft.name.strip():
        issues.append("name is required")

    resolved = _origin_resolved(draft, profile, issues)
    for t in draft.talents:
        row = klass.talent_table.rows[t.row_index] if klass.talent_table else None
        if row is None:
            continue
        resolved.append(resolve_option(row.option, t.choices, source=f"talent:{klass.key}/{t.option_key}#{t.roll_id[:8]}",
                                       issues=issues, table=klass.talent_table))
    modifiers = tuple(m for r in resolved for m in r.modifiers)
    features = tuple(f for r in resolved for f in r.features)
    origin_mods = [m for m in modifiers if not m.source.startswith("talent:")]
    if _roll_basis(origin_mods) != draft.roll_basis:
        issues.append("choices that affect hit point or talent rolls changed after rolling; restore them")
    pending = [p for r in resolved for p in r.pending]

    scores = dict(draft.base_scores)
    for m in modifiers:
        if m.target == ModTarget.STAT_SCORE and m.scope.stat is not None:
            scores[m.scope.stat.value] += m.amount
    stat_mods = _stat_mods(scores, profile)

    hp_rule, hp_ref = profile.get("character.hit_points", HitPointRule)
    refs.append(hp_ref)
    hp = max(hp_rule.minimum, (draft.hp_roll_total or 0) + stat_mods[hp_rule.add_stat])
    hp += collect(modifiers, ModTarget.HIT_POINTS, Scope(), stat_mods=stat_mods, level=1).bonus

    ac_rule, ac_ref = profile.get("character.armor_class", ArmorClassRule)
    refs.append(ac_ref)
    armor_class = ac_rule.base + (stat_mods[ac_rule.add_ability] if ac_rule.add_ability else 0)
    armor_class += collect(modifiers, ModTarget.ARMOR_CLASS, Scope(), stat_mods=stat_mods, level=1).bonus

    slot_bonus = collect(modifiers, ModTarget.GEAR_SLOTS, Scope(), stat_mods=stat_mods, level=1).bonus
    gear_slots, cap_ref = capacity_slots(profile, scores=scores, bonus_slots=slot_bonus)
    refs.append(cap_ref)

    alignments, al_ref = profile.optional("character.alignments", list[str])
    if alignments is not None:
        refs.append(al_ref)
        if draft.alignment not in alignments:
            issues.append(f"choose an alignment from {alignments}")
    deities, _ = profile.optional("character.deities", list[DeityDef])
    deity = next((d for d in deities or [] if d.key == draft.deity), None)
    if draft.deity and deity is None:
        issues.append(f"unknown deity {draft.deity!r}")
    if klass.requires_deity:
        if deity is None:
            issues.append(f"{klass.name} must choose a deity")
        elif draft.alignment and deity.alignment != draft.alignment:
            issues.append(f"{klass.name} must serve a deity matching their alignment")
    backgrounds, _ = profile.optional("character.backgrounds", list[NamedEntry])
    if draft.background and backgrounds is not None and draft.background not in {b.key for b in backgrounds}:
        issues.append(f"unknown background {draft.background!r}")

    langs, _ = profile.optional("character.languages", LanguagesDef)
    grants = [g for g in ((ancestry.languages if ancestry else None), klass.languages) if g is not None]
    known = {x for g in grants for x in g.fixed}
    _check_languages(draft.languages, grants, langs, known, issues)

    if klass.spellcasting:
        tiers = klass.spellcasting.spells_known.get(1, ())
        if any(tiers):
            pending.append(f"spells: choose {list(tiers)} spells by tier once spellcasting is supported")

    if issues:
        raise ValidationFailed(issues)

    titles, _ = profile.optional("character.titles", list[TitleRow])
    title = next((t.title for t in titles or [] if t.class_key == klass.key and t.alignment == draft.alignment
                  and t.min_level <= 1 <= t.max_level), None)
    return CharacterBuild(
        name=draft.name.strip(), class_key=klass.key, class_name=klass.name,
        ancestry_key=ancestry.key if ancestry else None, ancestry_name=ancestry.name if ancestry else None,
        level=1, base_scores=dict(draft.base_scores), scores=scores,
        stat_modifiers={a.value: v for a, v in stat_mods.items()}, max_hp=hp, armor_class=armor_class,
        gear_slots=gear_slots, languages=tuple(sorted(known | set(draft.languages))), alignment=draft.alignment,
        deity=draft.deity, background=draft.background, title=title,
        modifiers=modifiers,  # STAT_SCORE entries are already folded into scores; kept for audit
        features=features, pending=tuple(pending), gold_amount=draft.gold_amount, gold_unit=draft.gold_unit,
        rule_refs=tuple(dict.fromkeys(str(r) for r in refs)))


__all__ = [
    "CharacterBuild", "CreationDraft", "Resolved", "TalentResult", "can_reroll_stats", "choose_origin",
    "finalize", "reroll_stats", "resolve_option", "roll_class", "set_manual_scores", "start_draft",
]
