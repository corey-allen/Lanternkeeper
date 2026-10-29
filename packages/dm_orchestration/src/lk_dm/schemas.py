"""Typed schemas shared between orchestration and the API."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: Literal["system", "user", "assistant"]
    content: str


class ClaimClass(StrEnum):
    VERIFIED_SOURCE = "VERIFIED_SOURCE"  # directly supported by an approved passage
    VERIFIED_RULE = "VERIFIED_RULE"  # supported by the active rules profile
    CAMPAIGN_FACT = "CAMPAIGN_FACT"  # established by a committed game event
    INFERENCE = "INFERENCE"
    IMPROVISATION = "IMPROVISATION"
    UNKNOWN = "UNKNOWN"  # insufficient or conflicting evidence


class Visibility(StrEnum):
    DM_ONLY = "DM_ONLY"
    PLAYER_VISIBLE = "PLAYER_VISIBLE"
    CHARACTER_KNOWN = "CHARACTER_KNOWN"
    NPC_PRIVATE = "NPC_PRIVATE"


class PassageRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    passage_id: str
    document_id: str
    page_number: int


class ContextItem(BaseModel):
    """One unit of retrieved context, already filtered for the audience."""

    model_config = ConfigDict(frozen=True)
    kind: str  # e.g. location, npc, clue, passage, event, rule, npc_memory
    claim_class: ClaimClass
    title: str
    text: str
    citations: tuple[PassageRef, ...] = ()
    entity_id: str | None = None


class SceneOption(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str


class SceneOptions(BaseModel):
    """The legal targets the model may refer to. Ids outside these lists are rejected."""

    model_config = ConfigDict(frozen=True)
    location: SceneOption
    exits: tuple[SceneOption, ...] = ()
    npcs: tuple[SceneOption, ...] = ()
    enemies: tuple[SceneOption, ...] = ()
    items: tuple[SceneOption, ...] = ()
    abilities: tuple[str, ...] = ()
    difficulties: tuple[str, ...] = ()
    in_combat: bool = False


class IntentKind(StrEnum):
    ASK = "ask"  # question about the scene/world; no state change
    MOVE = "move"
    SEARCH = "search"
    ABILITY_CHECK = "ability_check"
    TALK = "talk"
    ATTACK = "attack"
    USE_ITEM = "use_item"
    WAIT = "wait"
    UNSUPPORTED = "unsupported"  # recognised but not supported by the rules engine
    CLARIFY = "clarify"  # ambiguous; ask the player


class IntentProposal(BaseModel):
    """What the model believes the player wants. A proposal, never a result.

    There are deliberately no fields for dice results, damage, DCs, HP, or any
    other computed value: the model cannot supply them.
    """

    model_config = ConfigDict(extra="forbid")
    intent: IntentKind
    target_id: str | None = Field(default=None, max_length=64)
    ability: str | None = Field(default=None, max_length=8)
    difficulty: str | None = Field(default=None, max_length=16)
    item_key: str | None = Field(default=None, max_length=64)
    topic: str | None = Field(default=None, max_length=300)
    unsupported_mechanic: str | None = Field(default=None, max_length=64)
    clarification: str | None = Field(default=None, max_length=300)
    wait_minutes: int | None = Field(default=None, ge=1, le=600)
