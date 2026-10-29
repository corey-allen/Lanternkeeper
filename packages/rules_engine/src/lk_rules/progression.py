"""Quest state machine and tracked conditions.

These are campaign-logic transitions rather than game-system rules, so they do
not consult the rules profile (except the condition catalog).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .errors import InvalidTransition, ValidationFailed
from .profile import RulesProfile


class QuestStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    DISCOVERED = "DISCOVERED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


QUEST_TRANSITIONS: dict[QuestStatus, frozenset[QuestStatus]] = {
    QuestStatus.UNKNOWN: frozenset({QuestStatus.DISCOVERED, QuestStatus.ACTIVE}),
    QuestStatus.DISCOVERED: frozenset({QuestStatus.ACTIVE, QuestStatus.ABANDONED}),
    QuestStatus.ACTIVE: frozenset({QuestStatus.COMPLETED, QuestStatus.FAILED, QuestStatus.ABANDONED}),
    QuestStatus.COMPLETED: frozenset(),
    QuestStatus.FAILED: frozenset(),
    QuestStatus.ABANDONED: frozenset({QuestStatus.ACTIVE}),
}


def transition_quest(current: QuestStatus, target: QuestStatus) -> QuestStatus:
    if target not in QUEST_TRANSITIONS[current]:
        raise InvalidTransition(
            f"Quest cannot move from {current.value} to {target.value}",
            current=current.value, target=target.value,
        )
    return target


class TrackedCondition(BaseModel):
    """A condition tracked by name. Its mechanical effects are NOT implemented."""

    model_config = ConfigDict(frozen=True)
    key: str
    expires_at_minute: int | None = Field(default=None, ge=0)


def apply_condition(
    conditions: tuple[TrackedCondition, ...], key: str, *, profile: RulesProfile,
    expires_at_minute: int | None = None,
) -> tuple[TrackedCondition, ...]:
    catalog, _ = profile.get("conditions.catalog", list[str])
    if key not in catalog:
        raise ValidationFailed([f"unknown condition {key!r}; allowed: {catalog}"])
    kept = tuple(c for c in conditions if c.key != key)
    return (*kept, TrackedCondition(key=key, expires_at_minute=expires_at_minute))


def expire_conditions(
    conditions: tuple[TrackedCondition, ...], now_minute: int
) -> tuple[tuple[TrackedCondition, ...], tuple[TrackedCondition, ...]]:
    """Return (still_active, expired)."""
    active = tuple(c for c in conditions if c.expires_at_minute is None or c.expires_at_minute > now_minute)
    expired = tuple(c for c in conditions if c not in active)
    return active, expired
