from __future__ import annotations

import ast
import pathlib

import pytest

from lk_rules import DEV_HOUSE_RULES, SHADOWDARK_UNVERIFIED, Ability
from lk_rules.character import AbilityScores, CharacterSpec, StartingItem, ability_modifier, validate_character
from lk_rules.checks import CheckRequest, resolve_check
from lk_rules.clock import ClockState, advance, light_source
from lk_rules.combat import Combatant, CombatantStatus, EncounterStatus, next_turn, resolve_attack, start_encounter
from lk_rules.coverage import CoverageStatus, coverage, require_supported
from lk_rules.dice import DiceExpression, FixedRoller, SecureRoller, SeededRoller, roll, roll_d20_with_mode
from lk_rules.errors import (
    CombatError,
    InvalidDiceExpression,
    InvalidTransition,
    InventoryError,
    UnsupportedMechanic,
    UnverifiedRule,
    ValidationFailed,
)
from lk_rules.inventory import Inventory, add_item, remove_item
from lk_rules.progression import QuestStatus, TrackedCondition, apply_condition, expire_conditions, transition_quest

P = DEV_HOUSE_RULES
SCORES = AbilityScores(STR=12, DEX=14, CON=13, INT=10, WIS=15, CHA=8)


# ---- dice -------------------------------------------------------------------

@pytest.mark.parametrize("text,count,sides,mod", [
    ("d20", 1, 20, 0), ("2d6+1", 2, 6, 1), ("1d8-2", 1, 8, -2), ("3D4 + 2", 3, 4, 2),
])
def test_parse_dice(text, count, sides, mod):
    expr = DiceExpression.parse(text)
    assert expr.terms[0].count == count and expr.terms[0].sides == sides and expr.modifier == mod


@pytest.mark.parametrize("bad", ["", "d", "2d7", "abc", "1d20+", "1d20 1d6", "999d6", "d20;rm", "1d20+" + "1" * 70])
def test_reject_bad_dice(bad):
    with pytest.raises(InvalidDiceExpression):
        DiceExpression.parse(bad)


def test_roll_record_is_auditable():
    rec = roll("2d6+1", FixedRoller([3, 5]), purpose="damage", actor="pc")
    assert rec.total == 9 and [d.value for d in rec.dice] == [3, 5] and rec.modifier == 1
    assert rec.roller_kind == "fixed-test-only" and rec.roll_id


def test_seeded_roller_is_reproducible_and_secure_roller_in_range():
    a = [SeededRoller(7).roll_die(20) for _ in range(1)]
    b = [SeededRoller(7).roll_die(20) for _ in range(1)]
    assert a == b
    r = SecureRoller()
    assert r.kind == "secure"
    assert all(1 <= r.roll_die(6) <= 6 for _ in range(200))


def test_advantage_keeps_highest_and_disadvantage_lowest():
    adv = roll_d20_with_mode(FixedRoller([4, 17]), mode="advantage", modifier=1, purpose="t", actor="a")
    dis = roll_d20_with_mode(FixedRoller([4, 17]), mode="disadvantage", modifier=1, purpose="t", actor="a")
    assert adv.total == 18 and dis.total == 5
    assert [d.kept for d in adv.dice] == [False, True]


# ---- profiles and coverage --------------------------------------------------

def test_builtin_profiles_are_well_formed():
    assert P.validate_entries() == []
    assert SHADOWDARK_UNVERIFIED.validate_entries() == []


def test_unverified_profile_blocks_every_mechanic():
    with pytest.raises(UnverifiedRule):
        ability_modifier(10, SHADOWDARK_UNVERIFIED)
    spec = CharacterSpec(name="X", class_key="any", ability_scores=SCORES)
    with pytest.raises(UnverifiedRule):
        validate_character(spec, SHADOWDARK_UNVERIFIED)
    rows = {r.mechanic: r.status for r in coverage(SHADOWDARK_UNVERIFIED)}
    assert rows["check.ability"] == CoverageStatus.BLOCKED_UNVERIFIED
    assert rows["spell.cast"] == CoverageStatus.UNSUPPORTED


def test_unverified_profile_contains_no_values():
    assert all(e.value is None for e in SHADOWDARK_UNVERIFIED.entries.values())


def test_unsupported_mechanics_raise_typed_errors():
    for key in ("rest.recover", "spell.cast", "advancement.level", "nonexistent"):
        with pytest.raises(UnsupportedMechanic):
            require_supported(key)
    assert require_supported("check.ability").implemented


def test_modifier_table():
    assert [ability_modifier(s, P)[0] for s in (3, 8, 10, 13, 18)] == [-2, -1, 0, 1, 2]
    with pytest.raises(UnverifiedRule):
        ability_modifier(25, P)


# ---- character --------------------------------------------------------------

def test_validate_character_derives_stats():
    sheet = validate_character(
        CharacterSpec(name=" Ash ", class_key="wayfarer", ability_scores=SCORES,
                      starting_items=[StartingItem(item_key="torch", quantity=2),
                                      StartingItem(item_key="short_blade", quantity=1)]),
        P,
    )
    b = sheet.build
    assert b.name == "Ash" and b.max_hp == 9  # max d8 + CON(+1)
    assert b.armor_class == 11  # 10 + DEX(+1)
    assert sheet.inventory.quantity_of("torch") == 2
    assert any("ability.modifier_table" in r for r in b.rule_refs)


def test_validate_character_collects_all_issues():
    bad = AbilityScores(STR=2, DEX=19, CON=10, INT=10, WIS=10, CHA=10)
    with pytest.raises(ValidationFailed) as exc:
        validate_character(CharacterSpec(name="B", class_key="paladin", ability_scores=bad,
                                         starting_items=[StartingItem(item_key="laser", quantity=1)]), P)
    assert len(exc.value.issues) == 4


def test_character_capacity_enforced():
    with pytest.raises(InventoryError):
        validate_character(CharacterSpec(name="C", class_key="wayfarer", ability_scores=SCORES,
                                         starting_items=[StartingItem(item_key="rope", quantity=11)]), P)


def test_minimum_hp_is_one():
    frail = AbilityScores(STR=10, DEX=10, CON=3, INT=10, WIS=10, CHA=10)
    custom = P.model_copy(deep=True)
    custom.entries["character.classes"] = custom.entries["character.classes"].model_copy(
        update={"value": [{"key": "w", "name": "W", "hit_die": "1d2"}]})
    sheet = validate_character(CharacterSpec(name="F", class_key="w", ability_scores=frail), custom)
    assert sheet.build.max_hp == 1


# ---- checks -----------------------------------------------------------------

def test_check_success_and_failure_use_profile_dc():
    ok = resolve_check(CheckRequest(ability=Ability.WIS, difficulty="normal"), score=15, actor="pc",
                       profile=P, roller=FixedRoller([10]))
    assert ok.target == 11 and ok.modifier == 1 and ok.roll.total == 11 and ok.success
    fail = resolve_check(CheckRequest(ability=Ability.WIS, difficulty="hard"), score=15, actor="pc",
                         profile=P, roller=FixedRoller([10]))
    assert fail.target == 14 and not fail.success


def test_check_rejects_unknown_difficulty():
    with pytest.raises(ValidationFailed):
        resolve_check(CheckRequest(ability=Ability.STR, difficulty="DC 3"), score=10, actor="pc",
                      profile=P, roller=FixedRoller([20]))


def test_check_with_advantage():
    res = resolve_check(CheckRequest(ability=Ability.STR, difficulty="normal", mode="advantage"),
                        score=10, actor="pc", profile=P, roller=FixedRoller([2, 15]))
    assert res.roll.total == 15 and res.success


# ---- inventory --------------------------------------------------------------

def test_inventory_add_remove():
    inv = add_item(Inventory(), "torch", 3, P)
    inv = remove_item(inv, "torch", 1)
    assert inv.quantity_of("torch") == 2
    inv = remove_item(inv, "torch", 2)
    assert inv.stacks == ()


def test_inventory_errors():
    with pytest.raises(InventoryError):
        remove_item(Inventory(), "torch", 1)
    with pytest.raises(InventoryError):
        add_item(Inventory(), "unknown_item", 1, P)
    with pytest.raises(InventoryError):
        add_item(Inventory(), "rope", 11, P)
    with pytest.raises(InventoryError):
        add_item(Inventory(), "rope", 0, P)
    assert add_item(Inventory(), "rations", 3, P).slots_used == 2  # two per slot
    assert add_item(Inventory(), "pack", 2, P).slots_used == 1  # the first pack is free


# ---- time and light ---------------------------------------------------------

def test_light_burns_down_and_expires():
    state = light_source(ClockState(), item_key="torch", light_id="L1", profile=P)
    assert state.lights[0].remaining_minutes == 60 and state.is_lit
    step = advance(state, 50)
    assert step.after.lights[0].remaining_minutes == 10 and not step.expired_lights
    step = advance(step.after, 10)
    assert step.after.lights == () and step.expired_lights[0].light_id == "L1"
    assert step.after.minutes_elapsed == 60 and not step.after.is_lit


def test_time_validation_and_non_light():
    with pytest.raises(ValidationFailed):
        advance(ClockState(), -1)
    with pytest.raises(InventoryError):
        light_source(ClockState(), item_key="rope", light_id="x", profile=P)
    assert P.action_minutes("move")[0] == 10
    with pytest.raises(UnverifiedRule):
        P.action_minutes("teleport")


# ---- combat -----------------------------------------------------------------

def _fighters(enemy_hp: int = 4) -> list[Combatant]:
    return [
        Combatant(combatant_id="pc", name="Ash", side="player", armor_class=11, hp=9, max_hp=9,
                  attack_modifier=1, damage="1d6", initiative_modifier=1),
        Combatant(combatant_id="m1", name="Lurker", side="enemy", armor_class=12, hp=enemy_hp,
                  max_hp=enemy_hp, attack_modifier=2, damage="1d4", initiative_modifier=0),
    ]


def test_initiative_orders_highest_first():
    out = start_encounter(_fighters(), profile=P, roller=FixedRoller([5, 15]))
    assert [c.combatant_id for c in out.encounter.combatants] == ["m1", "pc"]
    assert len(out.rolls) == 2


def test_initiative_tie_goes_to_player():
    out = start_encounter(_fighters(), profile=P, roller=FixedRoller([9, 10]))  # pc 10, m1 10
    assert out.encounter.current.combatant_id == "pc"


def test_attack_hit_damage_and_victory():
    enc = start_encounter(_fighters(enemy_hp=4), profile=P, roller=FixedRoller([20, 1])).encounter
    out = resolve_attack(enc, attacker_id="pc", target_id="m1", profile=P, roller=FixedRoller([11, 4]))
    assert out.hit and out.attack_roll.total == 12 and out.damage == 4
    assert out.target_hp_after == 0 and out.target_status == CombatantStatus.DEFEATED
    assert out.encounter.status == EncounterStatus.PLAYER_VICTORY


def test_attack_miss_and_turns():
    enc = start_encounter(_fighters(), profile=P, roller=FixedRoller([20, 1])).encounter
    out = resolve_attack(enc, attacker_id="pc", target_id="m1", profile=P, roller=FixedRoller([10]))
    assert not out.hit and out.damage_roll is None and out.target_hp_after == 4
    enc2 = next_turn(out.encounter)
    assert enc2.current.combatant_id == "m1" and enc2.round == 1
    assert next_turn(enc2).round == 2
    with pytest.raises(CombatError):
        resolve_attack(enc2, attacker_id="pc", target_id="m1", profile=P, roller=FixedRoller([20]))


def test_player_at_zero_is_incapacitated_not_dead():
    fighters = _fighters()
    fighters[0] = fighters[0].model_copy(update={"hp": 1})
    enc = start_encounter(fighters, profile=P, roller=FixedRoller([1, 20])).encounter
    out = resolve_attack(enc, attacker_id="m1", target_id="pc", profile=P, roller=FixedRoller([15, 3]))
    assert out.target_status == CombatantStatus.INCAPACITATED
    assert out.encounter.status == EncounterStatus.PLAYER_DEFEAT


def test_encounter_validation():
    with pytest.raises(CombatError):
        start_encounter(_fighters()[:1], profile=P, roller=FixedRoller([1]))


# ---- quests and conditions --------------------------------------------------

def test_quest_transitions():
    s = transition_quest(QuestStatus.UNKNOWN, QuestStatus.DISCOVERED)
    s = transition_quest(s, QuestStatus.ACTIVE)
    assert transition_quest(s, QuestStatus.COMPLETED) == QuestStatus.COMPLETED
    with pytest.raises(InvalidTransition):
        transition_quest(QuestStatus.COMPLETED, QuestStatus.ACTIVE)
    with pytest.raises(InvalidTransition):
        transition_quest(QuestStatus.UNKNOWN, QuestStatus.COMPLETED)


def test_conditions_tracked_and_expire():
    conds = apply_condition((), "frightened", profile=P, expires_at_minute=30)
    conds = apply_condition(conds, "poisoned", profile=P)
    active, expired = expire_conditions(conds, 30)
    assert [c.key for c in active] == ["poisoned"] and expired == (TrackedCondition(key="frightened", expires_at_minute=30),)
    with pytest.raises(ValidationFailed):
        apply_condition((), "petrified", profile=P)


# ---- architecture boundary --------------------------------------------------

FORBIDDEN = {"fastapi", "sqlalchemy", "httpx", "openai", "anthropic", "lk_api", "lk_dm", "requests"}


def test_rules_engine_has_no_framework_dependencies():
    src = pathlib.Path(__file__).parents[1] / "src" / "lk_rules"
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in FORBIDDEN, f"{path.name} imports {name}"


# ---- profile validation and pinning ------------------------------------------

def _synthetic_verified():
    from datetime import date

    from lk_rules import RuleEntry, RuleStatus

    entries = dict(P.entries)
    entries["check.difficulty"] = RuleEntry(status=RuleStatus.VERIFIED, source="Synthetic Book p.7",
                                            value={"normal": 13}, verified_on=date(2026, 10, 29))
    return P.model_copy(update={"key": "synthetic-verified", "entries": entries})


def test_validate_profile_requires_citations_for_verified():
    from datetime import date

    from lk_rules import RuleEntry, RuleStatus, validate_profile

    assert validate_profile(P) == [] and validate_profile(SHADOWDARK_UNVERIFIED) == []
    good = _synthetic_verified()
    assert validate_profile(good) == []
    bad_entries = dict(good.entries)
    bad_entries["check.difficulty"] = RuleEntry(status=RuleStatus.VERIFIED, source="my memory", value={"normal": 13})
    problems = validate_profile(good.model_copy(update={"entries": bad_entries, "key": "Bad Key"}))
    assert any("cite a page" in p for p in problems) and any("verified_on" in p for p in problems)
    assert any("profile key" in p for p in problems)
    bad_entries["check.difficulty"] = RuleEntry(status=RuleStatus.VERIFIED, source="p.7", value="eleven",
                                                verified_on=date(2026, 1, 1))
    assert validate_profile(good.model_copy(update={"entries": bad_entries}))


def test_content_hash_is_stable_and_sensitive():
    a, b = _synthetic_verified(), _synthetic_verified()
    assert a.content_hash() == b.content_hash() and len(a.content_hash()) == 64
    assert a.content_hash() != P.content_hash()


def test_house_rule_acceptance_needed_for_any_house_rule():
    assert P.needs_house_rule_acceptance
    assert not SHADOWDARK_UNVERIFIED.needs_house_rule_acceptance
    assert _synthetic_verified().needs_house_rule_acceptance  # still has house-rule entries
