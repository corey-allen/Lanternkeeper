"""Light timing modes, multiple lights, fuel, and the real-time play clock (synthetic values)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lk_rules import DEV_HOUSE_RULES as P
from lk_rules import RuleEntry, RulesProfile, RuleStatus
from lk_rules.clock import (
    ActiveLight,
    ClockState,
    advance,
    credit_seconds,
    light_source,
    seconds_until_deadline,
    split_pending,
    time_cost,
    timing_options,
)
from lk_rules.definitions import LightTimingRule
from lk_rules.errors import InventoryError, UnverifiedRule, ValidationFailed


def profile_with(extra: dict[str, object]) -> RulesProfile:
    entries = dict(P.entries)
    for key, value in extra.items():
        entries[key] = RuleEntry(status=RuleStatus.HOUSE_RULE, source="house rule: synthetic test value", value=value)
    return P.model_copy(update={"entries": entries})


CATALOG = [*P.entries["items.catalog"].value,
           {"key": "lamp", "name": "Lamp", "category": "light", "light": {"duration_minutes": 45, "fuel_item_key": "lamp_oil"}},
           {"key": "lamp_oil", "name": "Lamp oil", "consumable": True}]
TIMED = profile_with({"time.light_timing": {"modes": ["real_time", "rounds"], "rounds_per_hour": 12,
                                            "multiple_sources": ["ride_along", "restart"]},
                      "items.catalog": CATALOG})


def lit(*remaining: int) -> ClockState:
    return ClockState(lights=tuple(ActiveLight(light_id=f"L{i}", item_key="torch", remaining_minutes=m)
                                   for i, m in enumerate(remaining)))


def test_light_burns_down_and_expires():
    state = light_source(ClockState(), item_key="torch", light_id="L1", profile=P).after
    assert state.lights[0].remaining_minutes == 60 and state.is_lit
    step = advance(state, 50)
    assert step.after.lights[0].remaining_minutes == 10 and not step.expired_lights
    step = advance(step.after, 10)
    assert step.after.lights == () and step.expired_lights[0].light_id == "L1"
    assert step.after.minutes_elapsed == 60 and not step.after.is_lit


def test_profile_without_timing_rule_keeps_action_minutes():
    opts = timing_options(P)
    assert opts.modes == ("action_minutes",) and opts.multiple_sources == ("independent",)
    assert time_cost(P, "action_minutes", "crawl", "move")[0] == P.action_minutes("move")[0]
    assert time_cost(P, "action_minutes", "combat_round", "combat_round", rounds=3)[0] == \
        3 * P.action_minutes("combat_round")[0]


def test_timing_modes_cost():
    opts = timing_options(TIMED)
    assert opts.modes == ("real_time", "rounds") and opts.rule_ref
    assert time_cost(TIMED, "real_time", "crawl", "move")[0] == 0
    assert time_cost(TIMED, "real_time", "combat_round", "combat_round", rounds=4)[0] == 0
    assert time_cost(TIMED, "rounds", "crawl", "move")[0] == 5  # 60 / 12
    assert time_cost(TIMED, "rounds", "combat_round", "combat_round", rounds=2)[0] == 10
    # "Time passes" always uses the profile's action minutes, in every mode.
    assert time_cost(TIMED, "real_time", "time_passes", "search")[0] == P.action_minutes("search")[0]
    with pytest.raises(UnverifiedRule):
        time_cost(P, "real_time", "crawl", "move")  # mode needs the timing rule


def test_mode_not_offered_is_rejected():
    only_real = profile_with({"time.light_timing": {"modes": ["real_time"], "rounds_per_hour": 12,
                                                    "multiple_sources": ["restart"]}})
    with pytest.raises(ValidationFailed):
        time_cost(only_real, "rounds", "crawl", "move")


def test_rounds_per_hour_must_give_whole_minutes():
    with pytest.raises(ValueError):
        LightTimingRule(modes=["rounds"], rounds_per_hour=7, multiple_sources=["restart"])


def test_ride_along_joins_the_current_timer():
    result = light_source(lit(20), item_key="torch", light_id="new", profile=TIMED, policy="ride_along")
    assert result.lit.remaining_minutes == 20 and not result.extinguished
    step = advance(result.after, 20)
    assert not step.after.is_lit and len(step.expired_lights) == 2


def test_ride_along_with_nothing_lit_starts_a_full_timer():
    result = light_source(ClockState(), item_key="torch", light_id="new", profile=TIMED, policy="ride_along")
    assert result.lit.remaining_minutes == 60


def test_restart_extinguishes_old_lights():
    result = light_source(lit(20, 20), item_key="torch", light_id="new", profile=TIMED, policy="restart")
    assert result.lit.remaining_minutes == 60
    assert [li.light_id for li in result.extinguished] == ["L0", "L1"]
    assert result.after.lights == (result.lit,)


def test_independent_lights_keep_their_own_timers():
    result = light_source(lit(20), item_key="torch", light_id="new", profile=P)
    assert sorted(li.remaining_minutes for li in result.after.lights) == [20, 60]


def test_fuelled_light_consumes_fuel_not_itself():
    result = light_source(ClockState(), item_key="lamp", light_id="x", profile=TIMED, policy="restart")
    assert result.consumes == "lamp_oil" and result.lit.remaining_minutes == 45
    assert light_source(ClockState(), item_key="torch", light_id="y", profile=TIMED).consumes == "torch"
    with pytest.raises(InventoryError):
        light_source(ClockState(), item_key="lamp_oil", light_id="z", profile=TIMED)


def test_credit_seconds_respects_idle_pause():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    assert credit_seconds(None, t0, 300) == 0  # clock paused
    assert credit_seconds(t0, t0 + timedelta(seconds=30), 300) == 30
    assert credit_seconds(t0, t0 + timedelta(seconds=300), 300) == 300
    assert credit_seconds(t0, t0 + timedelta(seconds=301), 300) == 0  # app closed or idle: nothing burns
    assert credit_seconds(t0, t0 - timedelta(seconds=5), 300) == 0  # out-of-order heartbeat


def test_deadlines_and_pending_split():
    assert seconds_until_deadline(ClockState()) is None
    assert seconds_until_deadline(lit(3, 7)) == 180
    state = ClockState(minutes_elapsed=10, lights=lit(30).lights)
    assert seconds_until_deadline(state, [12]) == 120  # a condition expiring at minute 12
    assert split_pending(150) == (2, 30)
    with pytest.raises(ValidationFailed):
        split_pending(-1)


def test_total_darkness_affects_only_unlit_sight_tasks():
    from lk_rules.checks import darkness_effect

    dark = profile_with({"vision.total_darkness": {"sight_checks": "disadvantage"}})
    mode, ref = darkness_effect(dark, is_lit=False, needs_sight=True)
    assert mode == "disadvantage" and ref and ref.endswith("vision.total_darkness")
    assert darkness_effect(dark, is_lit=True, needs_sight=True) == ("normal", None)
    assert darkness_effect(dark, is_lit=False, needs_sight=False) == ("normal", None)
    assert darkness_effect(dark, is_lit=False, needs_sight=True, darkness_adapted=True) == ("normal", None)
    assert darkness_effect(P, is_lit=False, needs_sight=True) == ("normal", None)  # not part of this system
