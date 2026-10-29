"""Relational data model.

Three strictly separated layers:

* Source & ingestion  - what was uploaded and what the pipeline proposed.
* Adventure           - human-approved, provenance-backed definitions (read-only in play).
* Campaign            - per-campaign mutable state, events, and action records.

Rules tables mirror the rules-engine profiles for auditability and house rules.
Location connectivity lives ONLY in ``location_connection`` (maps will read it later).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.schema import Computed

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def enum(cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(cls, name=name, native_enum=False, length=40, create_constraint=True,
                  validate_strings=True, values_callable=lambda e: [m.value for m in e])


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


# =============================================================================
# Enums
# =============================================================================


class RightsStatus(StrEnum):
    ORIGINAL_WORK = "ORIGINAL_WORK"
    PERSONAL_COPY = "PERSONAL_COPY"
    LICENSED = "LICENSED"
    UNKNOWN = "UNKNOWN"


class DocumentKind(StrEnum):
    ADVENTURE_MODULE = "ADVENTURE_MODULE"
    ZINE = "ZINE"
    OTHER = "OTHER"


class DocumentStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    FAILED = "FAILED"


class ExtractionMethod(StrEnum):
    TEXT = "TEXT"
    OCR = "OCR"
    NONE = "NONE"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class EntityType(StrEnum):
    LOCATION = "LOCATION"
    NPC = "NPC"
    ENCOUNTER = "ENCOUNTER"
    MONSTER = "MONSTER"
    CLUE = "CLUE"
    SECRET = "SECRET"
    QUEST = "QUEST"
    STORY_BEAT = "STORY_BEAT"
    TREASURE = "TREASURE"
    HAZARD = "HAZARD"
    FACTION = "FACTION"


class CandidateType(StrEnum):
    LOCATION = "LOCATION"
    NPC = "NPC"
    ENCOUNTER = "ENCOUNTER"
    MONSTER = "MONSTER"
    CLUE = "CLUE"
    SECRET = "SECRET"
    QUEST = "QUEST"
    STORY_BEAT = "STORY_BEAT"
    TREASURE = "TREASURE"
    HAZARD = "HAZARD"
    FACTION = "FACTION"
    CONNECTION = "CONNECTION"
    EDGE = "EDGE"


class CandidateStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    UNCERTAIN = "UNCERTAIN"


class WarningCode(StrEnum):
    OCR_USED = "OCR_USED"
    OCR_UNAVAILABLE = "OCR_UNAVAILABLE"
    LOW_TEXT = "LOW_TEXT"
    DUPLICATE_ENTITY = "DUPLICATE_ENTITY"
    CONFLICTING_FACT = "CONFLICTING_FACT"
    BROKEN_REFERENCE = "BROKEN_REFERENCE"
    PROMPT_INJECTION_SUSPECTED = "PROMPT_INJECTION_SUSPECTED"
    NO_EVIDENCE = "NO_EVIDENCE"
    PAGE_LIMIT = "PAGE_LIMIT"


class Visibility(StrEnum):
    DM_ONLY = "DM_ONLY"
    PLAYER_VISIBLE = "PLAYER_VISIBLE"
    CHARACTER_KNOWN = "CHARACTER_KNOWN"
    NPC_PRIVATE = "NPC_PRIVATE"


class EdgeType(StrEnum):
    CONTAINS = "CONTAINS"  # location -> thing in it (npc, monster, clue, treasure, hazard)
    GUARDS = "GUARDS"
    TRIGGERS = "TRIGGERS"
    REQUIRES = "REQUIRES"
    REVEALS = "REVEALS"  # clue -> secret / quest / location
    KNOWS = "KNOWS"  # npc -> clue / secret
    HOSTILE_TO = "HOSTILE_TO"
    ALLIED_WITH = "ALLIED_WITH"
    LEADS_TO = "LEADS_TO"
    HAS_REWARD = "HAS_REWARD"
    PRECEDES = "PRECEDES"
    BLOCKED_BY = "BLOCKED_BY"
    MEMBER_OF = "MEMBER_OF"


class Disposition(StrEnum):
    HOSTILE = "hostile"
    WARY = "wary"
    NEUTRAL = "neutral"
    FRIENDLY = "friendly"
    HELPFUL = "helpful"


class RevealTrigger(StrEnum):
    ON_ENTER = "on_enter"
    ON_SEARCH = "on_search"
    ON_TALK = "on_talk"
    MANUAL = "manual"


class CampaignStatus(StrEnum):
    SETUP = "SETUP"
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"


class QuestStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    DISCOVERED = "DISCOVERED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


class NpcStatus(StrEnum):
    ACTIVE = "active"
    DEFEATED = "defeated"
    ABSENT = "absent"


class LearnedVia(StrEnum):
    SOURCE_DEFINED = "SOURCE_DEFINED"  # stated in the approved adventure text
    OBSERVED = "OBSERVED"  # the NPC witnessed the event
    TOLD = "TOLD"  # the player or someone told the NPC
    EVIDENCE = "EVIDENCE"


class EncounterStatus(StrEnum):
    ACTIVE = "active"
    PLAYER_VICTORY = "player_victory"
    PLAYER_DEFEAT = "player_defeat"


class ParticipantKind(StrEnum):
    CHARACTER = "character"
    MONSTER = "monster"
    NPC = "npc"


class ActionStatus(StrEnum):
    COMMITTED = "COMMITTED"  # state changed
    ANSWERED = "ANSWERED"  # read-only answer
    REJECTED = "REJECTED"  # proposal failed validation
    UNSUPPORTED = "UNSUPPORTED"
    CLARIFY = "CLARIFY"
    FAILED = "FAILED"  # provider or internal failure before commit; key may be retried


class CoverageStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    BLOCKED_UNVERIFIED = "BLOCKED_UNVERIFIED"
    UNSUPPORTED = "UNSUPPORTED"


# =============================================================================
# Users
# =============================================================================


class User(Base, TimestampMixin):
    __tablename__ = "app_user"
    id: Mapped[uuid.UUID] = pk()
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# =============================================================================
# Source & ingestion
# =============================================================================


class Adventure(Base, TimestampMixin):
    __tablename__ = "adventure"
    id: Mapped[uuid.UUID] = pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    graph_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class SourceDocument(Base, TimestampMixin):
    __tablename__ = "source_document"
    id: Mapped[uuid.UUID] = pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    adventure_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adventure.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    document_kind: Mapped[DocumentKind] = mapped_column(enum(DocumentKind, "document_kind"), nullable=False)
    rights_status: Mapped[RightsStatus] = mapped_column(enum(RightsStatus, "rights_status"), nullable=False)
    rights_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    rights_acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), default="PRIVATE", nullable=False)
    allow_remote_ai: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(enum(DocumentStatus, "document_status"), nullable=False)
    __table_args__ = (
        UniqueConstraint("owner_id", "sha256", name="uq_document_owner_hash"),
        CheckConstraint("visibility = 'PRIVATE'", name="ck_document_private"),
    )


class SourcePage(Base):
    __tablename__ = "source_page"
    __table_args__ = (UniqueConstraint("document_id", "page_number"),
                      CheckConstraint("page_number >= 1", name="ck_page_number"))
    id: Mapped[uuid.UUID] = pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_document.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    extraction_method: Mapped[ExtractionMethod] = mapped_column(enum(ExtractionMethod, "extraction_method"))
    raw_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ocr_confidence: Mapped[float | None] = mapped_column()
    width: Mapped[float | None] = mapped_column()
    height: Mapped[float | None] = mapped_column()


class SourcePassage(Base):
    __tablename__ = "source_passage"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal"),
        Index("ix_passage_tsv", "search_tsv", postgresql_using="gin"),
    )
    id: Mapped[uuid.UUID] = pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_document.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_page.id", ondelete="CASCADE"))
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    bbox: Mapped[list[float] | None] = mapped_column(JSONType)
    extraction_method: Mapped[ExtractionMethod] = mapped_column(enum(ExtractionMethod, "extraction_method"))
    injection_flags: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    search_tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', coalesce(heading,'') || ' ' || normalized_text)",
                           persisted=True))


class IngestionJob(Base, TimestampMixin):
    __tablename__ = "ingestion_job"
    __table_args__ = (CheckConstraint("attempts >= 0", name="ck_job_attempts"),)
    id: Mapped[uuid.UUID] = pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_document.id", ondelete="CASCADE"), index=True)
    status: Mapped[JobStatus] = mapped_column(enum(JobStatus, "job_status"), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    extractor: Mapped[str] = mapped_column(String(40), default="heuristic", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(500))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)


class ExtractionCandidate(Base, TimestampMixin):
    __tablename__ = "extraction_candidate"
    id: Mapped[uuid.UUID] = pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_job.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_document.id", ondelete="CASCADE"), index=True)
    adventure_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adventure.id", ondelete="CASCADE"), index=True)
    candidate_type: Mapped[CandidateType] = mapped_column(enum(CandidateType, "candidate_type"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    status: Mapped[CandidateStatus] = mapped_column(enum(CandidateStatus, "candidate_status"), nullable=False)
    edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reviewer_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    promoted_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("adventure_entity.id", ondelete="SET NULL"))
    promoted_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("location_connection.id", ondelete="SET NULL"))
    promoted_edge_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("adventure_edge.id", ondelete="SET NULL"))
    evidence: Mapped[list[CandidateEvidence]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class CandidateEvidence(Base):
    __tablename__ = "candidate_evidence"
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction_candidate.id", ondelete="CASCADE"), primary_key=True)
    passage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_passage.id", ondelete="CASCADE"), primary_key=True)


class IngestionWarning(Base, TimestampMixin):
    __tablename__ = "ingestion_warning"
    id: Mapped[uuid.UUID] = pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_job.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_document.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("extraction_candidate.id", ondelete="CASCADE"))
    passage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_passage.id", ondelete="CASCADE"))
    code: Mapped[WarningCode] = mapped_column(enum(WarningCode, "warning_code"), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)


# =============================================================================
# Adventure (approved definitions)
# =============================================================================


class AdventureEntity(Base, TimestampMixin):
    __tablename__ = "adventure_entity"
    __table_args__ = (UniqueConstraint("adventure_id", "entity_type", "normalized_name"),)
    id: Mapped[uuid.UUID] = pk()
    adventure_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adventure.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[EntityType] = mapped_column(enum(EntityType, "entity_type"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    visibility: Mapped[Visibility] = mapped_column(enum(Visibility, "visibility"), nullable=False)
    source_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    approved_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    evidence: Mapped[list[EntityEvidence]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class EntityEvidence(Base):
    """Provenance: every approved entity cites at least one passage (enforced in review service)."""

    __tablename__ = "entity_evidence"
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adventure_entity.id", ondelete="CASCADE"), primary_key=True)
    passage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_passage.id", ondelete="CASCADE"), primary_key=True)


def _detail_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("adventure_entity.id", ondelete="CASCADE"), primary_key=True)


class Location(Base):
    __tablename__ = "location"
    entity_id: Mapped[uuid.UUID] = _detail_fk()
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    area_code: Mapped[str] = mapped_column(String(16), default="", nullable=False)


class LocationConnection(Base):
    __tablename__ = "location_connection"
    __table_args__ = (
        UniqueConstraint("from_location_id", "to_location_id"),
        CheckConstraint("from_location_id <> to_location_id", name="ck_connection_not_self"),
    )
    id: Mapped[uuid.UUID] = pk()
    adventure_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adventure.id", ondelete="CASCADE"), index=True)
    from_location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("location.entity_id", ondelete="CASCADE"))
    to_location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("location.entity_id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    bidirectional: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    evidence_passage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_passage.id", ondelete="CASCADE"))


class NPCDefinition(Base):
    __tablename__ = "npc_definition"
    entity_id: Mapped[uuid.UUID] = _detail_fk()
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    motivation: Mapped[str] = mapped_column(Text, default="", nullable=False)
    default_disposition: Mapped[Disposition] = mapped_column(enum(Disposition, "disposition"), nullable=False)


class EncounterDefinition(Base):
    __tablename__ = "encounter_definition"
    entity_id: Mapped[uuid.UUID] = _detail_fk()
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    trigger: Mapped[str] = mapped_column(String(300), default="", nullable=False)


class MonsterDefinition(Base):
    """Stats are adventure source facts (from the approved passage), not rules-engine values."""

    __tablename__ = "monster_definition"
    __table_args__ = (CheckConstraint("hit_points >= 1", name="ck_monster_hp"),)
    entity_id: Mapped[uuid.UUID] = _detail_fk()
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    armor_class: Mapped[int] = mapped_column(Integer, nullable=False)
    hit_points: Mapped[int] = mapped_column(Integer, nullable=False)
    attack_bonus: Mapped[int] = mapped_column(Integer, nullable=False)
    damage: Mapped[str] = mapped_column(String(32), nullable=False)
    initiative_modifier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ClueDefinition(Base):
    __tablename__ = "clue_definition"
    entity_id: Mapped[uuid.UUID] = _detail_fk()
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reveal_trigger: Mapped[RevealTrigger] = mapped_column(enum(RevealTrigger, "reveal_trigger"), nullable=False)
    check_ability: Mapped[str | None] = mapped_column(String(8))
    check_difficulty: Mapped[str | None] = mapped_column(String(16))


class SecretDefinition(Base):
    __tablename__ = "secret_definition"
    entity_id: Mapped[uuid.UUID] = _detail_fk()
    content: Mapped[str] = mapped_column(Text, nullable=False)


class QuestDefinition(Base):
    __tablename__ = "quest_definition"
    entity_id: Mapped[uuid.UUID] = _detail_fk()
    objective: Mapped[str] = mapped_column(Text, nullable=False)


class StoryBeat(Base):
    __tablename__ = "story_beat"
    entity_id: Mapped[uuid.UUID] = _detail_fk()
    description: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(String(300), default="", nullable=False)


class TreasureDefinition(Base):
    __tablename__ = "treasure_definition"
    entity_id: Mapped[uuid.UUID] = _detail_fk()
    description: Mapped[str] = mapped_column(Text, nullable=False)


class HazardDefinition(Base):
    __tablename__ = "hazard_definition"
    entity_id: Mapped[uuid.UUID] = _detail_fk()
    description: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(String(300), default="", nullable=False)


class FactionDefinition(Base):
    __tablename__ = "faction_definition"
    entity_id: Mapped[uuid.UUID] = _detail_fk()
    description: Mapped[str] = mapped_column(Text, nullable=False)
    goals: Mapped[str] = mapped_column(Text, default="", nullable=False)


class AdventureEdge(Base):
    """Typed relationship between entities. Location connectivity is NOT stored here."""

    __tablename__ = "adventure_edge"
    __table_args__ = (
        UniqueConstraint("source_entity_id", "target_entity_id", "edge_type"),
        CheckConstraint("source_entity_id <> target_entity_id", name="ck_edge_not_self"),
    )
    id: Mapped[uuid.UUID] = pk()
    adventure_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adventure.id", ondelete="CASCADE"), index=True)
    source_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adventure_entity.id", ondelete="CASCADE"), index=True)
    target_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adventure_entity.id", ondelete="CASCADE"), index=True)
    edge_type: Mapped[EdgeType] = mapped_column(enum(EdgeType, "edge_type"), nullable=False)
    conditions: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    evidence_passage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_passage.id", ondelete="CASCADE"))


DETAIL_TABLES: dict[EntityType, type[Base]] = {
    EntityType.LOCATION: Location,
    EntityType.NPC: NPCDefinition,
    EntityType.ENCOUNTER: EncounterDefinition,
    EntityType.MONSTER: MonsterDefinition,
    EntityType.CLUE: ClueDefinition,
    EntityType.SECRET: SecretDefinition,
    EntityType.QUEST: QuestDefinition,
    EntityType.STORY_BEAT: StoryBeat,
    EntityType.TREASURE: TreasureDefinition,
    EntityType.HAZARD: HazardDefinition,
    EntityType.FACTION: FactionDefinition,
}


# =============================================================================
# Rules
# =============================================================================


class RulesProfileRow(Base, TimestampMixin):
    __tablename__ = "rules_profile"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # profile key
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    system: Mapped[str] = mapped_column(String(32), nullable=False)
    requires_house_rule_acceptance: Mapped[bool] = mapped_column(Boolean, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))  # current version
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RulesProfileVersion(Base, TimestampMixin):
    """Immutable copy of one profile version. Campaigns pin to a row here, never to a file."""

    __tablename__ = "rules_profile_version"
    __table_args__ = (UniqueConstraint("profile_id", "content_hash"),)
    id: Mapped[uuid.UUID] = pk()
    profile_id: Mapped[str] = mapped_column(ForeignKey("rules_profile.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)


class RuleReference(Base):
    __tablename__ = "rule_reference"
    __table_args__ = (UniqueConstraint("profile_id", "rule_key"),)
    id: Mapped[uuid.UUID] = pk()
    profile_id: Mapped[str] = mapped_column(ForeignKey("rules_profile.id", ondelete="CASCADE"))
    rule_key: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_citation: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class HouseRule(Base, TimestampMixin):
    """A user decision to adopt a rule value. Recorded per campaign."""

    __tablename__ = "house_rule"
    id: Mapped[uuid.UUID] = pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("rules_profile.id"))
    rule_key: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))


class RulesCoverageRecord(Base):
    __tablename__ = "rules_coverage"
    __table_args__ = (UniqueConstraint("profile_id", "mechanic_key"),)
    id: Mapped[uuid.UUID] = pk()
    profile_id: Mapped[str] = mapped_column(ForeignKey("rules_profile.id", ondelete="CASCADE"))
    mechanic_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[CoverageStatus] = mapped_column(enum(CoverageStatus, "coverage_status"), nullable=False)
    blocking_rules: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


# =============================================================================
# Campaign state
# =============================================================================


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaign"
    __table_args__ = (CheckConstraint("version >= 0", name="ck_campaign_version"),)
    id: Mapped[uuid.UUID] = pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    adventure_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("adventure.id", ondelete="RESTRICT"))
    rules_profile_id: Mapped[str] = mapped_column(ForeignKey("rules_profile.id"), nullable=False)
    rules_profile_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rules_profile_version.id", ondelete="RESTRICT"))
    house_rules_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[CampaignStatus] = mapped_column(enum(CampaignStatus, "campaign_status"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("location.entity_id", ondelete="RESTRICT"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Character(Base, TimestampMixin):
    __tablename__ = "character"
    __table_args__ = (
        CheckConstraint("hp_current >= 0 AND hp_current <= hp_max", name="ck_character_hp"),
        CheckConstraint("hp_max >= 1", name="ck_character_hp_max"),
        CheckConstraint("level >= 1", name="ck_character_level"),
        *[CheckConstraint(f"{a} BETWEEN 1 AND 30", name=f"ck_character_{a}")
          for a in ("str_score", "dex_score", "con_score", "int_score", "wis_score", "cha_score")],
    )
    id: Mapped[uuid.UUID] = pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    class_key: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    str_score: Mapped[int] = mapped_column(Integer, nullable=False)
    dex_score: Mapped[int] = mapped_column(Integer, nullable=False)
    con_score: Mapped[int] = mapped_column(Integer, nullable=False)
    int_score: Mapped[int] = mapped_column(Integer, nullable=False)
    wis_score: Mapped[int] = mapped_column(Integer, nullable=False)
    cha_score: Mapped[int] = mapped_column(Integer, nullable=False)
    hp_current: Mapped[int] = mapped_column(Integer, nullable=False)
    hp_max: Mapped[int] = mapped_column(Integer, nullable=False)
    armor_class: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_refs: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)


class CharacterInventory(Base):
    __tablename__ = "character_inventory"
    __table_args__ = (UniqueConstraint("character_id", "item_key"),
                      CheckConstraint("quantity > 0", name="ck_inventory_quantity"),
                      CheckConstraint("slots_each >= 0", name="ck_inventory_slots"))
    id: Mapped[uuid.UUID] = pk()
    character_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("character.id", ondelete="CASCADE"), index=True)
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    slots_each: Mapped[int] = mapped_column(Integer, nullable=False)
    equipped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class CharacterCondition(Base):
    __tablename__ = "character_condition"
    __table_args__ = (UniqueConstraint("character_id", "condition_key"),)
    id: Mapped[uuid.UUID] = pk()
    character_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("character.id", ondelete="CASCADE"), index=True)
    condition_key: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at_minute: Mapped[int | None] = mapped_column(Integer)


class CampaignLocationState(Base):
    __tablename__ = "campaign_location_state"
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), primary_key=True)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("location.entity_id", ondelete="CASCADE"), primary_key=True)
    visited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_visited_minute: Mapped[int | None] = mapped_column(Integer)
    searched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class CampaignDiscovery(Base):
    """An adventure entity (clue, secret, hidden exit...) revealed to the player in this campaign."""

    __tablename__ = "campaign_discovery"
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adventure_entity.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("game_event.id"), nullable=False)


class NPCInstance(Base):
    __tablename__ = "npc_instance"
    __table_args__ = (UniqueConstraint("campaign_id", "npc_entity_id"),)
    id: Mapped[uuid.UUID] = pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), index=True)
    npc_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adventure_entity.id", ondelete="CASCADE"))
    disposition: Mapped[Disposition] = mapped_column(enum(Disposition, "disposition"), nullable=False)
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("location.entity_id", ondelete="SET NULL"))
    status: Mapped[NpcStatus] = mapped_column(enum(NpcStatus, "npc_status"), nullable=False)


class NPCMemory(Base, TimestampMixin):
    __tablename__ = "npc_memory"
    __table_args__ = (
        CheckConstraint("event_id IS NOT NULL OR passage_id IS NOT NULL", name="ck_memory_has_origin"),
    )
    id: Mapped[uuid.UUID] = pk()
    npc_instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("npc_instance.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    learned_via: Mapped[LearnedVia] = mapped_column(enum(LearnedVia, "learned_via"), nullable=False)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("game_event.id"))
    passage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_passage.id", ondelete="CASCADE"))
    learned_at_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    reliability: Mapped[str] = mapped_column(String(16), default="certain", nullable=False)
    shared_with_player: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class QuestState(Base):
    __tablename__ = "quest_state"
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), primary_key=True)
    quest_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adventure_entity.id", ondelete="CASCADE"),
                                                       primary_key=True)
    status: Mapped[QuestStatus] = mapped_column(enum(QuestStatus, "quest_status"), nullable=False)
    updated_event_id: Mapped[int | None] = mapped_column(ForeignKey("game_event.id"))


class EncounterState(Base, TimestampMixin):
    __tablename__ = "encounter_state"
    id: Mapped[uuid.UUID] = pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), index=True)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("location.entity_id", ondelete="CASCADE"))
    status: Mapped[EncounterStatus] = mapped_column(enum(EncounterStatus, "encounter_status"), nullable=False)
    round: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    turn_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    participants: Mapped[list[EncounterParticipant]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="EncounterParticipant.turn_order")
    __table_args__ = (
        Index("uq_one_active_encounter", "campaign_id", unique=True,
              postgresql_where="status = 'active'"),
    )


class EncounterParticipant(Base):
    __tablename__ = "encounter_participant"
    __table_args__ = (CheckConstraint("hp >= 0 AND hp <= max_hp", name="ck_participant_hp"),
                      UniqueConstraint("encounter_id", "turn_order"))
    id: Mapped[uuid.UUID] = pk()
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("encounter_state.id", ondelete="CASCADE"), index=True)
    kind: Mapped[ParticipantKind] = mapped_column(enum(ParticipantKind, "participant_kind"), nullable=False)
    character_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("character.id", ondelete="CASCADE"))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("adventure_entity.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    turn_order: Mapped[int] = mapped_column(Integer, nullable=False)
    initiative: Mapped[int] = mapped_column(Integer, nullable=False)
    armor_class: Mapped[int] = mapped_column(Integer, nullable=False)
    hp: Mapped[int] = mapped_column(Integer, nullable=False)
    max_hp: Mapped[int] = mapped_column(Integer, nullable=False)
    attack_modifier: Mapped[int] = mapped_column(Integer, nullable=False)
    damage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class DefeatedCreature(Base):
    """Monsters defeated in this campaign, so they do not respawn."""

    __tablename__ = "defeated_creature"
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("adventure_entity.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("game_event.id"), nullable=False)


class WorldClock(Base):
    __tablename__ = "world_clock"
    __table_args__ = (CheckConstraint("minutes_elapsed >= 0", name="ck_clock_nonnegative"),)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), primary_key=True)
    minutes_elapsed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ActiveLight(Base):
    __tablename__ = "active_light"
    __table_args__ = (CheckConstraint("remaining_minutes > 0", name="ck_light_remaining"),)
    id: Mapped[uuid.UUID] = pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), index=True)
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    remaining_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    lit_at_minute: Mapped[int] = mapped_column(Integer, nullable=False)


class ActionRecord(Base, TimestampMixin):
    __tablename__ = "action_record"
    __table_args__ = (UniqueConstraint("campaign_id", "idempotency_key", name="uq_action_idempotency"),)
    id: Mapped[uuid.UUID] = pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    player_input: Mapped[str] = mapped_column(Text, nullable=False)
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[ActionStatus] = mapped_column(enum(ActionStatus, "action_status"), nullable=False)
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    diagnostics: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    narration: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))


class GameEvent(Base, TimestampMixin):
    """Append-only. UPDATE is blocked by a database trigger; DELETE only during campaign purge."""

    __tablename__ = "game_event"
    __table_args__ = (UniqueConstraint("campaign_id", "sequence"),)
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True,
                                    autoincrement=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    campaign_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    visibility: Mapped[Visibility] = mapped_column(enum(Visibility, "visibility"), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    # No ON DELETE actions here: they would UPDATE rows, which the append-only trigger forbids.
    action_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("action_record.id"))
    game_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("location.entity_id"))


class DiceRoll(Base, TimestampMixin):
    __tablename__ = "dice_roll"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), index=True)
    action_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("action_record.id", ondelete="SET NULL"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("game_event.id"))
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    purpose: Mapped[str] = mapped_column(String(80), nullable=False)
    expression: Mapped[str] = mapped_column(String(64), nullable=False)
    dice: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False)
    modifier: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_refs: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    roller_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    committed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CampaignSnapshot(Base, TimestampMixin):
    """A recovery/performance checkpoint. Never replaces the event log or canonical tables."""

    __tablename__ = "campaign_snapshot"
    id: Mapped[uuid.UUID] = pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign.id", ondelete="CASCADE"), index=True)
    campaign_version: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
