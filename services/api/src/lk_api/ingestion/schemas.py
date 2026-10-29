"""Typed candidate payloads. Used for extraction output and for reviewer edits alike."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from lk_rules.dice import DiceExpression
from lk_rules.profile import Ability

from ..models import CandidateType, Disposition, EdgeType, RevealTrigger, Visibility

Short = Annotated[str, Field(max_length=200)]
Long = Annotated[str, Field(max_length=4000)]


def _dice(value: str) -> str:
    DiceExpression.parse(value)
    return value


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: Visibility | None = None  # None = default for the type


class LocationData(_Base):
    description: Long = ""
    area_code: Annotated[str, Field(max_length=16)] = ""


class NPCData(_Base):
    description: Long = ""
    motivation: Long = ""
    disposition: Disposition = Disposition.NEUTRAL
    location: Short | None = None
    knows: list[Short] = Field(default_factory=list, max_length=20)


class MonsterData(_Base):
    description: Long = ""
    armor_class: int = Field(ge=1, le=40)
    hit_points: int = Field(ge=1, le=1000)
    attack_bonus: int = Field(ge=-10, le=30)
    damage: Annotated[str, AfterValidator(_dice)]
    initiative_modifier: int = Field(default=0, ge=-10, le=20)
    location: Short | None = None


class ClueData(_Base):
    content: Long
    reveal_trigger: RevealTrigger = RevealTrigger.ON_SEARCH
    check_ability: Ability | None = None
    check_difficulty: Annotated[str, Field(max_length=16)] | None = None
    location: Short | None = None
    reveals: list[Short] = Field(default_factory=list, max_length=10)


class SecretData(_Base):
    content: Long
    location: Short | None = None


class QuestData(_Base):
    objective: Long


class DescribedData(_Base):
    description: Long
    trigger: Short = ""
    location: Short | None = None


class FactionData(_Base):
    description: Long
    goals: Long = ""


class ConnectionData(_Base):
    from_location: Short
    to_location: Short
    label: Annotated[str, Field(max_length=120)] = ""
    hidden: bool = False
    bidirectional: bool = True


class EdgeData(_Base):
    source: Short
    source_type: CandidateType
    target: Short
    target_type: CandidateType | None = None
    edge_type: EdgeType
    conditions: Short = ""


DATA_SCHEMAS: dict[CandidateType, type[_Base]] = {
    CandidateType.LOCATION: LocationData,
    CandidateType.NPC: NPCData,
    CandidateType.MONSTER: MonsterData,
    CandidateType.CLUE: ClueData,
    CandidateType.SECRET: SecretData,
    CandidateType.QUEST: QuestData,
    CandidateType.ENCOUNTER: DescribedData,
    CandidateType.STORY_BEAT: DescribedData,
    CandidateType.TREASURE: DescribedData,
    CandidateType.HAZARD: DescribedData,
    CandidateType.FACTION: FactionData,
    CandidateType.CONNECTION: ConnectionData,
    CandidateType.EDGE: EdgeData,
}


def validate_data(candidate_type: CandidateType, data: dict) -> dict:
    return DATA_SCHEMAS[candidate_type].model_validate(data).model_dump(mode="json")
