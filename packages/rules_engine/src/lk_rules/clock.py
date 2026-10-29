"""Game time and light-source durations, measured in whole game minutes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .errors import InventoryError, ValidationFailed
from .profile import RulesProfile

MAX_ADVANCE_MINUTES = 7 * 24 * 60


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


def light_source(state: ClockState, *, item_key: str, light_id: str, profile: RulesProfile) -> ClockState:
    item_def, _ = profile.item(item_key)
    if item_def is None or item_def.light is None:
        raise InventoryError(f"{item_key!r} is not a light source", item_key=item_key)
    light = ActiveLight(light_id=light_id, item_key=item_key,
                        remaining_minutes=item_def.light.duration_minutes)
    return state.model_copy(update={"lights": (*state.lights, light)})
