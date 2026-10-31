"""Game time, light sources, and the real-time play clock (ADR-0013).

Game time is kept in whole minutes. In real-time mode the server credits active play seconds as
*pending* time and folds whole minutes into the game clock at defined points; the functions here
are the pure parts of that: how much a heartbeat gap is worth, when pending time must be flushed,
and what an action costs in each timing mode.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .definitions import LightTimingRule, MultipleLights, TimingMode
from .errors import InventoryError, ValidationFailed
from .profile import RuleRef, RulesProfile

MAX_ADVANCE_MINUTES = 7 * 24 * 60

# Profiles without ``time.light_timing`` keep the original model: every action costs profile minutes.
CampaignTiming = TimingMode | Literal["action_minutes"]
LightPolicy = MultipleLights | Literal["independent"]
TimeKind = Literal["crawl", "combat_round", "time_passes"]


class ActiveLight(BaseModel):
    model_config = ConfigDict(frozen=True)
    light_id: str
    item_key: str
    remaining_minutes: int = Field(ge=0)


class ClockState(BaseModel):
    model_config = ConfigDict(frozen=True)
    minutes_elapsed: int = Field(ge=0, default=0)
    lights: tuple[ActiveLight, ...] = ()

    @property
    def is_lit(self) -> bool:
        return any(light.remaining_minutes > 0 for light in self.lights)


class ClockAdvance(BaseModel):
    model_config = ConfigDict(frozen=True)
    before: ClockState
    after: ClockState
    minutes: int
    expired_lights: tuple[ActiveLight, ...]


def advance(state: ClockState, minutes: int) -> ClockAdvance:
    if minutes < 0 or minutes > MAX_ADVANCE_MINUTES:
        raise ValidationFailed([f"cannot advance time by {minutes} minutes"])
    remaining: list[ActiveLight] = []
    expired: list[ActiveLight] = []
    for light in state.lights:
        left = light.remaining_minutes - minutes
        if left <= 0:
            expired.append(light.model_copy(update={"remaining_minutes": 0}))
        else:
            remaining.append(light.model_copy(update={"remaining_minutes": left}))
    after = ClockState(minutes_elapsed=state.minutes_elapsed + minutes, lights=tuple(remaining))
    return ClockAdvance(before=state, after=after, minutes=minutes, expired_lights=tuple(expired))


# ---- timing modes ----------------------------------------------------------------------------


class TimingOptions(BaseModel):
    model_config = ConfigDict(frozen=True)
    modes: tuple[CampaignTiming, ...]
    multiple_sources: tuple[LightPolicy, ...]
    rule_ref: str | None = None


def timing_options(profile: RulesProfile) -> TimingOptions:
    rule, ref = profile.optional("time.light_timing", LightTimingRule)
    if rule is None:
        return TimingOptions(modes=("action_minutes",), multiple_sources=("independent",))
    return TimingOptions(modes=tuple(rule.modes), multiple_sources=tuple(rule.multiple_sources), rule_ref=str(ref))


def time_cost(profile: RulesProfile, mode: CampaignTiming, kind: TimeKind, action: str, *,
              rounds: int = 1) -> tuple[int, list[RuleRef]]:
    """Game minutes an action costs, and the rules that say so.

    ``action`` names the ``time.action_minutes`` entry used for "time passes" actions, and for every
    action in ``action_minutes`` mode.
    """
    if kind == "time_passes" or mode == "action_minutes":
        minutes, ref = profile.action_minutes(action)
        return minutes * rounds, [ref]
    rule, ref = profile.get("time.light_timing", LightTimingRule)
    if mode not in rule.modes:
        raise ValidationFailed([f"timing mode {mode!r} is not offered by this profile"])
    if mode == "real_time":
        return 0, [ref]  # covered by elapsed real time
    return rule.minutes_per_round * rounds, [ref]


# ---- lighting ----------------------------------------------------------------------------------


class LightResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    after: ClockState
    lit: ActiveLight
    extinguished: tuple[ActiveLight, ...] = ()
    consumes: str  # item key removed from inventory: the light item, or its fuel


def light_source(state: ClockState, *, item_key: str, light_id: str, profile: RulesProfile,
                 policy: LightPolicy = "independent") -> LightResult:
    item_def, _ = profile.item(item_key)
    if item_def is None or item_def.light is None:
        raise InventoryError(f"{item_key!r} is not a light source", item_key=item_key)
    duration = item_def.light.duration_minutes
    burning = [li for li in state.lights if li.remaining_minutes > 0]
    extinguished: tuple[ActiveLight, ...] = ()
    if policy == "ride_along" and burning:
        lit = ActiveLight(light_id=light_id, item_key=item_key,
                          remaining_minutes=max(li.remaining_minutes for li in burning))
        lights = (*state.lights, lit)
    elif policy == "restart":
        lit = ActiveLight(light_id=light_id, item_key=item_key, remaining_minutes=duration)
        extinguished, lights = tuple(state.lights), (lit,)
    else:
        lit = ActiveLight(light_id=light_id, item_key=item_key, remaining_minutes=duration)
        lights = (*state.lights, lit)
    return LightResult(after=state.model_copy(update={"lights": lights}), lit=lit, extinguished=extinguished,
                       consumes=item_def.light.fuel_item_key or item_key)


# ---- real-time play clock ----------------------------------------------------------------------


def credit_seconds(last_tick_at: datetime | None, now: datetime, idle_pause_seconds: int) -> int:
    """Active seconds a heartbeat gap is worth: the whole gap if within the idle pause, else none."""
    if last_tick_at is None:
        return 0
    gap = (now - last_tick_at).total_seconds()
    if gap <= 0 or gap > idle_pause_seconds:
        return 0
    return round(gap)


def seconds_until_deadline(state: ClockState, condition_expiries: list[int] | tuple[int, ...] = ()) -> int | None:
    """Active seconds from the last flushed minute until a light or condition expires."""
    candidates = [li.remaining_minutes * 60 for li in state.lights if li.remaining_minutes > 0]
    candidates += [max(0, m - state.minutes_elapsed) * 60 for m in condition_expiries]
    return min(candidates) if candidates else None


def split_pending(pending_seconds: int) -> tuple[int, int]:
    """Whole minutes to flush, and the seconds carried over."""
    if pending_seconds < 0:
        raise ValidationFailed(["pending seconds cannot be negative"])
    return divmod(pending_seconds, 60)


__all__ = ["ActiveLight", "CampaignTiming", "ClockAdvance", "ClockState", "LightPolicy", "LightResult",
           "TimeKind", "TimingOptions", "advance", "credit_seconds", "light_source", "seconds_until_deadline",
           "split_pending", "time_cost", "timing_options"]
