"""Checks with modifiers, task categories, natural results and automatic success (synthetic values)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from lk_rules import DEV_HOUSE_RULES, Ability
from lk_rules.checks import CheckRequest, automatic_success, resolve_check, trained_auto_success
from lk_rules.dice import FixedRoller
from lk_rules.errors import ValidationFailed
from lk_rules.modifiers import Feature, FeatureKind, ModMode, Modifier, ModTarget, Scope

_spec = importlib.util.spec_from_file_location("syn", Path(__file__).with_name("test_creation.py"))
syn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(syn)
P = syn.P  # normal DC 11 (dev table), natural 20/1 auto results, tags force/tracking/climbing

FORCE_ADV = Modifier(target=ModTarget.STAT_CHECK, mode=ModMode.ADVANTAGE,
                     scope=Scope(stat=Ability.STR, check_tag="force"), source="class:x/grit")
STR_BONUS = Modifier(target=ModTarget.STAT_CHECK, amount=2, scope=Scope(stat=Ability.STR), source="item:belt")
DARK = Modifier(target=ModTarget.STAT_CHECK, mode=ModMode.DISADVANTAGE, scope=Scope(condition="total_darkness"),
                source="rule:darkness")


def check(faces, *, tag=None, mods=(), mode="normal", conditions=(), difficulty="normal", score=10):
    return resolve_check(CheckRequest(ability=Ability.STR, difficulty=difficulty, task_tag=tag, mode=mode,
                                      conditions=conditions),
                         score=score, actor="pc", profile=P, roller=FixedRoller(faces), modifiers=mods)


def test_scoped_advantage_applies_only_to_its_task():
    forced = check([3, 15], tag="force", mods=[FORCE_ADV])
    assert forced.roll.expression == "2d20kh1" and forced.success
    plain = check([3], tag="climbing", mods=[FORCE_ADV])
    assert plain.roll.expression == "d20" and not plain.success


def test_stat_bonus_and_breakdown():
    res = check([9], mods=[STR_BONUS])
    assert res.modifier == 2 and res.roll.total == 11 and res.success
    assert [a.source for a in res.applied] == ["item:belt"]


def test_advantage_and_disadvantage_cancel_across_sources():
    res = check([3], tag="force", mods=[FORCE_ADV, DARK], conditions=("total_darkness",))
    assert res.roll.expression == "d20"
    res = check([3, 18], mode="advantage", mods=[DARK], conditions=("total_darkness",))
    assert res.roll.expression == "d20"  # requested advantage cancelled by darkness


def test_natural_results_from_profile():
    assert check([20], difficulty="extreme", score=3).success  # nat 20 auto success under this profile
    nat1 = check([1], score=18, mods=[Modifier(target=ModTarget.STAT_CHECK, amount=20, source="x")])
    assert nat1.natural == "min" and not nat1.success
    dev = resolve_check(CheckRequest(ability=Ability.STR, difficulty="normal"), score=18, actor="pc",
                        profile=DEV_HOUSE_RULES, roller=FixedRoller([1]),
                        modifiers=[Modifier(target=ModTarget.STAT_CHECK, amount=20, source="x")])
    assert dev.natural is None and dev.success  # dev house rules: a natural 1 is just a 1


def test_unknown_task_tag_rejected():
    with pytest.raises(ValidationFailed, match="task category"):
        check([10], tag="juggling")


def test_trained_tasks_auto_succeed_only_without_pressure():
    trained = (Feature(key="t", name="Trailwise", kind=FeatureKind.TRAINED_TASKS, tags=("tracking",), source="class:w"),)
    assert trained_auto_success(trained, "tracking", under_pressure=False) is not None
    assert trained_auto_success(trained, "tracking", under_pressure=True) is None
    assert trained_auto_success(trained, "climbing", under_pressure=False) is None
    assert trained_auto_success(trained, None, under_pressure=False) is None
    res = automatic_success(CheckRequest(ability=Ability.WIS, difficulty="hard", task_tag="tracking"), trained[0], P)
    assert res.automatic and res.success and res.roll is None
