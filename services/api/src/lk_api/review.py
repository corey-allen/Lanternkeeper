"""Candidate review: edit, approve (promote to authoritative data), reject.

Promotion is the ONLY path from extracted text to adventure facts, and it
requires a human decision plus at least one evidence passage.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .errors import ApiError, not_found
from .ingestion.schemas import validate_data
from .ingestion.text import normalize_key
from .ingestion.validation import similar
from .models import (
    DETAIL_TABLES,
    Adventure,
    AdventureEdge,
    AdventureEntity,
    CandidateStatus,
    CandidateType,
    ClueDefinition,
    EncounterDefinition,
    EntityEvidence,
    EntityType,
    ExtractionCandidate,
    FactionDefinition,
    HazardDefinition,
    Location,
    LocationConnection,
    MonsterDefinition,
    NPCDefinition,
    QuestDefinition,
    SecretDefinition,
    SourcePassage,
    StoryBeat,
    TreasureDefinition,
    User,
    Visibility,
)

DEFAULT_VISIBILITY: dict[EntityType, Visibility] = {
    EntityType.LOCATION: Visibility.PLAYER_VISIBLE,
    EntityType.NPC: Visibility.PLAYER_VISIBLE,
    EntityType.MONSTER: Visibility.PLAYER_VISIBLE,
    EntityType.ENCOUNTER: Visibility.DM_ONLY,
    EntityType.CLUE: Visibility.DM_ONLY,
    EntityType.SECRET: Visibility.DM_ONLY,
    EntityType.QUEST: Visibility.DM_ONLY,
    EntityType.STORY_BEAT: Visibility.DM_ONLY,
    EntityType.TREASURE: Visibility.DM_ONLY,
    EntityType.HAZARD: Visibility.DM_ONLY,
    EntityType.FACTION: Visibility.DM_ONLY,
}


def get_candidate(session: Session, user: User, candidate_id: uuid.UUID, *, lock: bool = False) -> ExtractionCandidate:
    stmt = (select(ExtractionCandidate).join(Adventure, Adventure.id == ExtractionCandidate.adventure_id)
            .where(ExtractionCandidate.id == candidate_id, Adventure.owner_id == user.id))
    if lock:
        stmt = stmt.with_for_update(of=ExtractionCandidate)
    cand = session.scalar(stmt)
    if cand is None:
        raise not_found("Candidate")
    return cand


def edit_candidate(session: Session, user: User, candidate_id: uuid.UUID, *, name: str | None,
                   data: dict[str, Any] | None, note: str | None) -> ExtractionCandidate:
    cand = get_candidate(session, user, candidate_id, lock=True)
    if cand.status in (CandidateStatus.APPROVED, CandidateStatus.REJECTED):
        raise ApiError(409, "CANDIDATE_FINAL", f"Candidate is already {cand.status.value}")
    if name is not None:
        if not name.strip():
            raise ApiError(422, "REQUEST_INVALID", "Name must not be empty")
        cand.name = name.strip()[:200]
        cand.normalized_name = normalize_key(cand.name)[:200]
    if data is not None:
        try:
            cand.data = validate_data(cand.candidate_type, {**cand.data, **data})
        except ValidationError as exc:
            raise ApiError(422, "CANDIDATE_DATA_INVALID", "Candidate fields are invalid",
                           issues=[e["msg"] for e in exc.errors()]) from exc
    if note is not None:
        cand.reviewer_note = note[:2000]
    cand.edited = True
    return cand


def reject_candidate(session: Session, user: User, candidate_id: uuid.UUID, note: str = "") -> ExtractionCandidate:
    cand = get_candidate(session, user, candidate_id, lock=True)
    if cand.status == CandidateStatus.APPROVED:
        raise ApiError(409, "CANDIDATE_FINAL", "Approved candidates cannot be rejected; retraction is not "
                                               "implemented yet (see docs/implementation-plan.md)")
    cand.status = CandidateStatus.REJECTED
    cand.reviewer_note = note[:2000]
    return cand


def _approved_entity(session: Session, adventure_id: uuid.UUID, name: str,
                     types: list[EntityType] | None) -> AdventureEntity | None:
    key = normalize_key(name)
    stmt = select(AdventureEntity).where(AdventureEntity.adventure_id == adventure_id)
    if types:
        stmt = stmt.where(AdventureEntity.entity_type.in_(types))
    matches = [e for e in session.scalars(stmt) if similar(key, e.normalized_name)]
    exact = [e for e in matches if e.normalized_name == key]
    return (exact or matches or [None])[0]


def _detail_row(etype: EntityType, entity_id: uuid.UUID, data: dict[str, Any]):
    match etype:
        case EntityType.LOCATION:
            return Location(entity_id=entity_id, description=data["description"], area_code=data["area_code"])
        case EntityType.NPC:
            return NPCDefinition(entity_id=entity_id, description=data["description"],
                                 motivation=data["motivation"], default_disposition=data["disposition"])
        case EntityType.MONSTER:
            return MonsterDefinition(entity_id=entity_id, description=data["description"],
                                     armor_class=data["armor_class"], hit_points=data["hit_points"],
                                     attack_bonus=data["attack_bonus"], damage=data["damage"],
                                     initiative_modifier=data["initiative_modifier"])
        case EntityType.CLUE:
            return ClueDefinition(entity_id=entity_id, content=data["content"],
                                  reveal_trigger=data["reveal_trigger"], check_ability=data["check_ability"],
                                  check_difficulty=data["check_difficulty"])
        case EntityType.SECRET:
            return SecretDefinition(entity_id=entity_id, content=data["content"])
        case EntityType.QUEST:
            return QuestDefinition(entity_id=entity_id, objective=data["objective"])
        case EntityType.FACTION:
            return FactionDefinition(entity_id=entity_id, description=data["description"], goals=data["goals"])
        case EntityType.ENCOUNTER:
            return EncounterDefinition(entity_id=entity_id, description=data["description"], trigger=data["trigger"])
        case EntityType.STORY_BEAT:
            return StoryBeat(entity_id=entity_id, description=data["description"], trigger=data["trigger"])
        case EntityType.TREASURE:
            return TreasureDefinition(entity_id=entity_id, description=data["description"])
        case EntityType.HAZARD:
            return HazardDefinition(entity_id=entity_id, description=data["description"], trigger=data["trigger"])
    raise AssertionError(etype)


def approve_candidate(session: Session, user: User, candidate_id: uuid.UUID) -> ExtractionCandidate:
    cand = get_candidate(session, user, candidate_id, lock=True)
    if cand.status == CandidateStatus.APPROVED:
        raise ApiError(409, "CANDIDATE_FINAL", "Candidate is already approved")
    if cand.status == CandidateStatus.REJECTED:
        raise ApiError(409, "CANDIDATE_FINAL", "Rejected candidates cannot be approved")
    if not cand.evidence:
        raise ApiError(422, "NO_EVIDENCE", "Candidates without a supporting passage cannot be approved")
    try:
        data = validate_data(cand.candidate_type, cand.data)
    except ValidationError as exc:
        raise ApiError(422, "CANDIDATE_DATA_INVALID", "Fix the candidate's fields before approving",
                       issues=[e["msg"] for e in exc.errors()]) from exc
    passages = session.scalars(select(SourcePassage).where(
        SourcePassage.id.in_([e.passage_id for e in cand.evidence]))).all()
    if any(p.injection_flags for p in passages):
        raise ApiError(422, "EVIDENCE_FLAGGED", "Evidence includes a passage flagged as possible prompt "
                                                "injection; edit the evidence or reject the candidate")
    first_passage = min(passages, key=lambda p: p.ordinal)
    adventure = session.get(Adventure, cand.adventure_id, with_for_update=True)
    assert adventure is not None

    if cand.candidate_type == CandidateType.CONNECTION:
        src = _approved_entity(session, cand.adventure_id, data["from_location"], [EntityType.LOCATION])
        dst = _approved_entity(session, cand.adventure_id, data["to_location"], [EntityType.LOCATION])
        missing = [n for n, e in ((data["from_location"], src), (data["to_location"], dst)) if e is None]
        if missing:
            raise ApiError(409, "UNRESOLVED_REFERENCE", "Approve both locations first", missing=missing)
        assert src and dst
        if src.id == dst.id:
            raise ApiError(422, "CANDIDATE_DATA_INVALID", "A location cannot connect to itself")
        exists = session.scalar(select(LocationConnection.id).where(or_(
            (LocationConnection.from_location_id == src.id) & (LocationConnection.to_location_id == dst.id),
            (LocationConnection.from_location_id == dst.id) & (LocationConnection.to_location_id == src.id))))
        if exists:
            raise ApiError(409, "DUPLICATE_APPROVED_ENTITY", "These locations are already connected")
        conn = LocationConnection(adventure_id=cand.adventure_id, from_location_id=src.id, to_location_id=dst.id,
                                  label=data["label"], hidden=data["hidden"], bidirectional=data["bidirectional"],
                                  evidence_passage_id=first_passage.id)
        session.add(conn)
        session.flush()
        cand.promoted_connection_id = conn.id
    elif cand.candidate_type == CandidateType.EDGE:
        src = _approved_entity(session, cand.adventure_id, data["source"], [EntityType(data["source_type"])])
        ttypes = [EntityType(data["target_type"])] if data.get("target_type") else None
        dst = _approved_entity(session, cand.adventure_id, data["target"], ttypes)
        missing = [n for n, e in ((data["source"], src), (data["target"], dst)) if e is None]
        if missing:
            raise ApiError(409, "UNRESOLVED_REFERENCE", "Approve both endpoints first", missing=missing)
        assert src and dst
        exists = session.scalar(select(AdventureEdge.id).where(
            AdventureEdge.source_entity_id == src.id, AdventureEdge.target_entity_id == dst.id,
            AdventureEdge.edge_type == data["edge_type"]))
        if exists:
            raise ApiError(409, "DUPLICATE_APPROVED_ENTITY", "This relationship already exists")
        edge = AdventureEdge(adventure_id=cand.adventure_id, source_entity_id=src.id, target_entity_id=dst.id,
                             edge_type=data["edge_type"], conditions=data["conditions"],
                             evidence_passage_id=first_passage.id)
        session.add(edge)
        session.flush()
        cand.promoted_edge_id = edge.id
    else:
        etype = EntityType(cand.candidate_type.value)
        key = normalize_key(cand.name)
        clash = session.scalar(select(AdventureEntity.id).where(
            AdventureEntity.adventure_id == cand.adventure_id, AdventureEntity.entity_type == etype,
            AdventureEntity.normalized_name == key))
        if clash:
            raise ApiError(409, "DUPLICATE_APPROVED_ENTITY",
                           f"An approved {etype.value} named '{cand.name}' already exists; reject or rename this one")
        summary = data.get("description") or data.get("content") or data.get("objective") or ""
        entity = AdventureEntity(
            adventure_id=cand.adventure_id, entity_type=etype, name=cand.name, normalized_name=key,
            summary=summary[:1000], visibility=data.get("visibility") or DEFAULT_VISIBILITY[etype],
            source_order=first_passage.ordinal, approved_by=user.id, approved_at=datetime.now(UTC))
        entity.evidence = [EntityEvidence(passage_id=e.passage_id) for e in cand.evidence]
        session.add(entity)
        session.flush()
        session.add(_detail_row(etype, entity.id, data))
        cand.promoted_entity_id = entity.id
    cand.status = CandidateStatus.APPROVED
    adventure.graph_version += 1
    return cand


def adventure_graph(session: Session, adventure_id: uuid.UUID) -> dict[str, Any]:
    """Approved graph with provenance and validation findings (DM/workbench view)."""
    entities = session.scalars(select(AdventureEntity).where(AdventureEntity.adventure_id == adventure_id)
                               .order_by(AdventureEntity.source_order)).all()
    passage_ids = {ev.passage_id for e in entities for ev in e.evidence}
    pages = dict(session.execute(select(SourcePassage.id, SourcePassage.page_number)
                                 .where(SourcePassage.id.in_(passage_ids))).all()) if passage_ids else {}
    out_entities = []
    for e in entities:
        detail = session.get(DETAIL_TABLES[e.entity_type], e.id)
        fields = {c.key: getattr(detail, c.key) for c in detail.__table__.columns if c.key != "entity_id"} if detail else {}
        out_entities.append({
            "id": str(e.id), "type": e.entity_type.value, "name": e.name, "visibility": e.visibility.value,
            "summary": e.summary, "fields": {k: (v.value if hasattr(v, "value") else v) for k, v in fields.items()},
            "citations": [{"passage_id": str(ev.passage_id), "page_number": pages.get(ev.passage_id)} for ev in e.evidence],
        })
    names = {e.id: e.name for e in entities}
    connections = session.scalars(select(LocationConnection).where(LocationConnection.adventure_id == adventure_id)).all()
    edges = session.scalars(select(AdventureEdge).where(AdventureEdge.adventure_id == adventure_id)).all()
    connected = {c.from_location_id for c in connections} | {c.to_location_id for c in connections}
    findings = [
        {"code": "ISOLATED_LOCATION", "message": f"Location '{e.name}' has no approved connections"}
        for e in entities if e.entity_type == EntityType.LOCATION and e.id not in connected
        and sum(1 for x in entities if x.entity_type == EntityType.LOCATION) > 1
    ]
    pending = session.scalar(select(func.count()).select_from(ExtractionCandidate).where(
        ExtractionCandidate.adventure_id == adventure_id,
        ExtractionCandidate.status.in_([CandidateStatus.PENDING, CandidateStatus.UNCERTAIN])))
    return {
        "entities": out_entities,
        "connections": [{"id": str(c.id), "from": str(c.from_location_id), "to": str(c.to_location_id),
                         "from_name": names.get(c.from_location_id), "to_name": names.get(c.to_location_id),
                         "label": c.label, "hidden": c.hidden, "bidirectional": c.bidirectional,
                         "evidence_passage_id": str(c.evidence_passage_id)} for c in connections],
        "edges": [{"id": str(x.id), "source": str(x.source_entity_id), "target": str(x.target_entity_id),
                   "source_name": names.get(x.source_entity_id), "target_name": names.get(x.target_entity_id),
                   "type": x.edge_type.value, "evidence_passage_id": str(x.evidence_passage_id)} for x in edges],
        "findings": findings,
        "pending_candidates": pending,
    }
