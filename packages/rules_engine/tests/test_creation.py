"""Character creation drafts and modifiers, tested on an invented synthetic system (no book values)."""

from __future__ import annotations

from datetime import date

import pytest

from lk_rules import DEV_HOUSE_RULES, Ability, RuleEntry, RulesProfile, RuleStatus, validate_profile
from lk_rules.creation import can_reroll_stats, choose_origin, finalize, reroll_stats, roll_class, start_draft
from lk_rules.dice import FixedRoller
from lk_rules.errors import ValidationFailed
from lk_rules.modifiers import ModMode, Modifier, ModTarget, Scope, collect

SRC = "Synthetic Test Book p.1"


def _v(value):
    return RuleEntry(status=RuleStatus.VERIFIED, source=SRC, value=value, verified_on=date(2026, 10, 29))


ANCESTRIES = [
    {"key": "stonekin", "name": "Stonekin", "languages": {"fixed": ["Tradespeak", "Deepcant"]},
     "traits": [{"key": "sturdy", "label": "Sturdy", "modifiers": [
         {"target": "HIT_POINTS", "amount": 1},
         {"target": "HIT_POINT_ROLL", "mode": "ADVANTAGE"}]}]},
    {"key": "fenfolk", "name": "Fenfolk", "languages": {"fixed": ["Tradespeak"], "choose_common": 1},
     "traits": [{"key": "keen", "label": "Keen", "kind": "alternatives", "alternatives": [
         {"key": "keen_aim", "label": "Aim", "modifiers": [
             {"target": "ATTACK_ROLL", "amount": 1, "scope": {"attack_kind": "ranged"}}]},
         {"key": "keen_mind", "label": "Mind", "modifiers": [
             {"target": "SPELLCASTING_CHECK", "amount": 1}]}]},
         {"key": "eager", "label": "Eager", "modifiers": [{"target": "TALENT_ROLLS_LEVEL_1", "amount": 1}]}]},
]

WARDEN_TALENTS = {"dice": "1d4", "rows": [
    {"min": 1, "max": 1, "option": {"key": "focus", "label": "Weapon focus", "choices": [{"param": "weapon_type"}],
                                    "modifiers": [{"target": "ATTACK_ROLL", "amount": 1,
                                                   "scope": {"weapon_type": "$weapon_type"}}]}},
    {"min": 2, "max": 2, "option": {"key": "might", "label": "+1 stat", "choices": [
        {"param": "stat", "allowed": ["STR", "CON"]}],
        "modifiers": [{"target": "STAT_SCORE", "amount": 1, "scope": {"stat": "$stat"}}]}},
    {"min": 3, "max": 3, "option": {"key": "trinket", "label": "Make a trinket", "kind": "unsupported",
                                    "reason": "item creation"}},
    {"min": 4, "max": 4, "option": {"key": "pick", "label": "Pick", "kind": "alternatives", "alternatives": [
        {"key": "any", "label": "Any talent", "kind": "choose_row"},
        {"key": "points", "label": "2 points", "kind": "distribute_stat_points", "points": 2}]}},
]}

CLASSES = [
    {"key": "warden", "name": "Warden", "hit_die": "1d6", "talent_table": WARDEN_TALENTS, "talent_rolls_level_1": 1,
     "features": [{"key": "packer", "label": "Packer", "modifiers": [
         {"target": "GEAR_SLOTS", "amount_from_stat": "CON", "positive_only": True}]},
         {"key": "stalwart", "label": "Stalwart", "choices": [{"param": "stat", "allowed": ["STR", "DEX"]}],
          "modifiers": [{"target": "STAT_CHECK", "mode": "ADVANTAGE", "scope": {"stat": "$stat", "check_tag": "force"}}]},
         {"key": "unflinching", "label": "Unflinching", "features": [
             {"key": "no_surprise", "name": "Cannot be surprised", "kind": "IMMUNITY_SURPRISE"}]},
         {"key": "trailwise", "label": "Trailwise", "features": [
             {"key": "trailwise", "name": "Trailwise", "kind": "TRAINED_TASKS", "tags": ["tracking"]}],
          "modifiers": [{"target": "STAT_CHECK", "mode": "ADVANTAGE", "scope": {"check_tag": "tracking"}}]}]},
    {"key": "chanter", "name": "Chanter", "hit_die": "1d4", "requires_deity": True,
     "languages": {"choose_from": ["Skytongue", "Undertongue"], "choose_from_count": 1},
     "spellcasting": {"tradition": "chant", "stat": "WIS", "spells_known": {"1": [2]}}},
]


def synthetic_profile(**overrides) -> RulesProfile:
    entries = dict(DEV_HOUSE_RULES.entries)
    entries.update({
        "stats.generation": _v({"method": "roll_in_order", "dice": "2d6", "reroll_if_none_at_least": 11,
                                "rerolls_allowed": 1}),
        "character.ancestries": _v(ANCESTRIES),
        "character.classes": _v(CLASSES),
        "character.hit_points": _v({"level_1": "roll_hit_die", "add_stat": "CON", "minimum": 1}),
        "character.languages": _v({"common": ["Tradespeak", "Marshtalk", "Hilltalk"], "rare": ["Skytongue"]}),
        "character.alignments": _v(["order", "balance", "ruin"]),
        "character.deities": _v([{"key": "lumen", "name": "Lumen", "alignment": "order"}]),
        "character.titles": _v([{"class_key": "warden", "alignment": "order", "min_level": 1, "max_level": 2,
                                 "title": "Gatewatch"}]),
        "character.starting_gold": _v({"dice": "1d6", "multiplier": 3, "unit": "gp"}),
        "inventory.capacity": _v({"method": "stat_or_floor", "stat": "STR", "floor": 9}),
        "check.task_tags": _v([{"key": "force", "name": "Forcing"}, {"key": "tracking", "name": "Tracking"},
                               {"key": "climbing", "name": "Climbing"}]),
        "check.natural_results": _v({"max_face": "auto_success", "min_face": "auto_failure"}),
    })
    entries.update(overrides)
    return DEV_HOUSE_RULES.model_copy(update={"key": "synthetic", "entries": entries})


P = synthetic_profile()


def test_synthetic_profile_is_valid():
    assert validate_profile(P) == []


def test_modifier_collection_scopes_and_cancellation():
    mods = [
        Modifier(target=ModTarget.ATTACK_ROLL, amount=1, scope=Scope(attack_kind="melee"), source="a"),
        Modifier(target=ModTarget.ATTACK_ROLL, amount=2, scope=Scope(weapon_type="axe"), source="b"),
        Modifier(target=ModTarget.ATTACK_ROLL, amount_from_stat=Ability.STR, source="c"),
        Modifier(target=ModTarget.ATTACK_ROLL, amount=1, per_level_divisor=2, scope=Scope(weapon_type="axe"), source="d"),
        Modifier(target=ModTarget.ATTACK_ROLL, mode=ModMode.ADVANTAGE, scope=Scope(condition="high_ground"), source="e"),
        Modifier(target=ModTarget.ATTACK_ROLL, mode=ModMode.DISADVANTAGE, scope=Scope(condition="high_ground"), source="f"),
    ]
    stat_mods = {a: 0 for a in Ability} | {Ability.STR: 2}
    axe = collect(mods, ModTarget.ATTACK_ROLL, Scope(attack_kind="melee", weapon_type="axe"), stat_mods=stat_mods, level=5)
    assert axe.bonus == 1 + 2 + 2 + (1 + 2) and axe.roll_mode == "normal"
    assert {a.source for a in axe.applied} == {"a", "b", "c", "d"}
    bow = collect(mods, ModTarget.ATTACK_ROLL, Scope(attack_kind="ranged", condition="high_ground"),
                  stat_mods=stat_mods, level=1)
    assert bow.bonus == 2 and bow.advantage and bow.disadvantage and bow.roll_mode == "normal"  # cancel
    neg = Modifier(target=ModTarget.GEAR_SLOTS, amount_from_stat=Ability.CON, positive_only=True, source="g")
    assert collect([neg], ModTarget.GEAR_SLOTS, Scope(), stat_mods={a: -1 for a in Ability}, level=1).bonus == 0


def _draft(faces: list[int]):
    return start_draft(P, FixedRoller(faces))


def test_stats_are_rolled_in_order_and_reroll_only_when_allowed():
    low = [1, 1] * 6  # every stat 2
    draft, rolls = _draft(low)
    assert draft.base_scores == {a.value: 2 for a in Ability} and len(rolls) == 6 and draft.stage == "origin"
    assert can_reroll_stats(draft, P)
    draft, rolls = reroll_stats(draft, P, FixedRoller([6, 6] * 6))
    assert draft.base_scores["STR"] == 12 and draft.rerolls_used == 1
    with pytest.raises(ValidationFailed):
        reroll_stats(draft, P, FixedRoller([1] * 12))  # scores are high now, and only one reroll allowed


def _rolled(ancestry="stonekin", klass="warden", stats=None, class_faces=None):
    faces = stats or [5, 6, 3, 3, 6, 6, 4, 4, 3, 3, 4, 4]  # STR 11 DEX 6 CON 12 INT 8 WIS 6 CHA 8
    draft, _ = start_draft(P, FixedRoller(faces))
    draft = choose_origin(draft, P, ancestry_key=ancestry, class_key=klass)
    draft, rolls = roll_class(draft, P, FixedRoller(class_faces or [2, 5, 2, 4]))
    return draft, rolls


def test_roll_class_uses_advantage_from_ancestry_and_records_rolls():
    draft, rolls = _rolled()
    hp_roll = rolls[0]
    assert hp_roll.expression == "2d6kh1" and draft.hp_roll_total == 5  # advantage keeps 5 over 2
    assert draft.talents[0].option_key == "might"  # talent die showed 2
    assert draft.gold_amount == 12 and draft.gold_unit == "gp"  # 4 x 3
    with pytest.raises(ValidationFailed):
        choose_origin(draft, P, ancestry_key="fenfolk", class_key="warden")  # locked after rolling


def test_finalize_resolves_choices_and_derives_values():
    draft, _ = _rolled()
    draft = draft.model_copy(update={
        "name": "Bryn", "alignment": "order",
        "trait_choices": {"stalwart": {"stat": "STR"}},
    })
    draft.talents[0].choices.update({"stat": "CON"})
    build = finalize(draft, P)
    assert build.scores["CON"] == 13 and build.base_scores["CON"] == 12  # talent folded in
    assert build.max_hp == 5 + 1 + 1  # roll + CON mod (13 -> +1 in dev table) + Sturdy
    assert build.gear_slots == 11 + 1  # STR 11 over floor 9, + CON mod from Packer
    assert build.title == "Gatewatch" and "Deepcant" in build.languages
    assert any(f.kind == "IMMUNITY_SURPRISE" for f in build.features)
    adv = [m for m in build.modifiers if m.target == ModTarget.STAT_CHECK]
    assert adv[0].scope.stat == Ability.STR and adv[0].scope.check_tag == "force"


def test_finalize_reports_every_problem():
    draft, _ = _rolled()
    with pytest.raises(ValidationFailed) as exc:
        finalize(draft, P)
    text = " ".join(exc.value.issues)
    assert "name is required" in text and "alignment" in text and "choose stat" in text


def test_talent_alternatives_choose_row_and_distribution():
    draft, _ = _rolled(class_faces=[2, 5, 4, 4])  # talent die shows 4 -> "Pick"
    base = {"name": "Ash", "alignment": "order", "trait_choices": {"stalwart": {"stat": "DEX"}}}
    draft = draft.model_copy(update=base)
    draft.talents[0].choices.update({"alternative": 0, "row": 0, "row_choices": {"weapon_type": "spear"}})
    build = finalize(draft, P)
    assert any(m.scope.weapon_type == "spear" for m in build.modifiers)
    draft.talents[0].choices.clear()
    draft.talents[0].choices.update({"alternative": 0, "row": 3})
    with pytest.raises(ValidationFailed, match="cannot be chosen"):
        finalize(draft, P)
    draft.talents[0].choices.clear()
    draft.talents[0].choices.update({"alternative": 1, "distribute": {"STR": 1, "WIS": 1}})
    build = finalize(draft, P)
    assert build.scores["STR"] == 12 and build.scores["WIS"] == 7
    draft.talents[0].choices.update({"distribute": {"STR": 3}})
    with pytest.raises(ValidationFailed, match="exactly 2"):
        finalize(draft, P)


def test_unsupported_talent_is_recorded_as_pending_not_invented():
    draft, _ = _rolled(class_faces=[2, 5, 3, 4])  # talent 3 = unsupported
    draft = draft.model_copy(update={"name": "Ash", "alignment": "order",
                                     "trait_choices": {"stalwart": {"stat": "STR"}}})
    build = finalize(draft, P)
    assert any("not supported" in p for p in build.pending)


def test_extra_talent_roll_alternative_trait_and_languages():
    draft, rolls = _rolled(ancestry="fenfolk", class_faces=[3, 1, 2, 4])
    assert len(draft.talents) == 2  # Eager grants an extra level-1 talent roll
    draft = draft.model_copy(update={"name": "Wen", "alignment": "balance",
                                     "trait_choices": {"keen": {"alternative": 0}, "stalwart": {"stat": "DEX"}},
                                     "languages": ["Hilltalk"]})
    draft.talents[0].choices.update({"weapon_type": "sling"})
    draft.talents[1].choices.update({"stat": "STR"})
    build = finalize(draft, P)
    assert any(m.target == ModTarget.ATTACK_ROLL and m.scope.attack_kind == "ranged" for m in build.modifiers)
    assert set(build.languages) == {"Tradespeak", "Hilltalk"} and build.title is None
    bad = draft.model_copy(update={"languages": ["Skytongue"]})
    with pytest.raises(ValidationFailed, match="languages"):
        finalize(bad, P)


def test_deity_requirement_and_spell_choices_deferred():
    draft, _ = _rolled(klass="chanter", class_faces=[1, 1, 4])
    draft = draft.model_copy(update={"name": "Ivo", "alignment": "balance", "deity": "lumen",
                                     "languages": ["Undertongue"]})
    with pytest.raises(ValidationFailed, match="matching their alignment"):
        finalize(draft, P)
    build = finalize(draft.model_copy(update={"alignment": "order"}), P)
    assert any("spells" in p for p in build.pending) and build.max_hp == 2  # roll 1 + CON 12 (+0) + Sturdy 1


def test_unverified_profile_blocks_creation():
    from lk_rules import SHADOWDARK_UNVERIFIED
    from lk_rules.errors import UnverifiedRule

    with pytest.raises(UnverifiedRule):
        start_draft(SHADOWDARK_UNVERIFIED, FixedRoller([1]))


def test_changing_roll_affecting_choices_after_rolling_is_rejected():
    draft, _ = _rolled(ancestry="fenfolk", class_faces=[3, 1, 2, 4])
    base = {"name": "Wen", "alignment": "balance", "languages": ["Hilltalk"],
            "trait_choices": {"keen": {"alternative": 1}, "stalwart": {"stat": "DEX"}}}
    draft = draft.model_copy(update=base)
    draft.talents[0].choices.update({"weapon_type": "sling"})
    draft.talents[1].choices.update({"stat": "STR"})
    assert finalize(draft, P).name == "Wen"  # non-roll choices may change after rolling
    tampered = draft.model_copy(update={"roll_basis": []})
    with pytest.raises(ValidationFailed, match="changed after rolling"):
        finalize(tampered, P)


def test_starting_gear_is_bought_with_rolled_gold():
    from lk_rules.gear import Purchase

    draft, _ = _rolled()  # gold roll 4 x 3 = 12 gp
    base = {"name": "Bryn", "alignment": "order", "trait_choices": {"stalwart": {"stat": "STR"}}}
    draft = draft.model_copy(update={**base, "purchases": [Purchase(item_key="short_blade", quantity=1),
                                                           Purchase(item_key="travel_kit", quantity=1)]})
    draft.talents[0].choices.update({"stat": "CON"})
    build = finalize(draft, P)
    assert {s.item_key for s in build.items} >= {"short_blade", "torch", "pack"} and build.coins == {"gp": 4}
    broke = draft.model_copy(update={"purchases": [Purchase(item_key="padded_coat", quantity=2)]})
    with pytest.raises(ValidationFailed, match="starting gear"):
        finalize(broke, P)
