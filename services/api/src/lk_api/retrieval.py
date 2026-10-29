"""Source-grounded retrieval.

Filtering happens BEFORE ranking: only passages of this campaign's adventure,
cited by approved entities the player is allowed to see, and not flagged as
possible prompt injection, are ever candidates. A passage also cited by a
hidden (undiscovered DM-only) entity is excluded entirely (conservative).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lk_dm.schemas import ClaimClass, ContextItem, PassageRef

from .models import AdventureEntity, EntityEvidence, SourceDocument, SourcePassage, Visibility


@dataclass(frozen=True)
class PassageHit:
    passage_id: uuid.UUID
    document_id: uuid.UUID
    page_number: int
    heading: str
    text: str
    rank: float

    def ref(self) -> PassageRef:
        return PassageRef(passage_id=str(self.passage_id), document_id=str(self.document_id),
                          page_number=self.page_number)


def visible_entity_ids(session: Session, adventure_id: uuid.UUID, discovered: set[uuid.UUID]) -> tuple[set, set]:
    rows = session.execute(select(AdventureEntity.id, AdventureEntity.visibility)
                           .where(AdventureEntity.adventure_id == adventure_id)).all()
    visible = {i for i, v in rows if v == Visibility.PLAYER_VISIBLE or i in discovered}
    hidden = {i for i, _ in rows} - visible
    return visible, hidden


def allowed_passage_ids(session: Session, adventure_id: uuid.UUID, discovered: set[uuid.UUID]) -> set[uuid.UUID]:
    visible, hidden = visible_entity_ids(session, adventure_id, discovered)
    ev = session.execute(select(EntityEvidence.entity_id, EntityEvidence.passage_id)
                         .join(AdventureEntity, AdventureEntity.id == EntityEvidence.entity_id)
                         .where(AdventureEntity.adventure_id == adventure_id)).all()
    allowed = {p for e, p in ev if e in visible}
    blocked = {p for e, p in ev if e in hidden}
    return allowed - blocked


def passages_for_entities(session: Session, entity_ids: list[uuid.UUID], allowed: set[uuid.UUID]) -> list[PassageHit]:
    if not entity_ids:
        return []
    rows = session.execute(
        select(SourcePassage).join(EntityEvidence, EntityEvidence.passage_id == SourcePassage.id)
        .where(EntityEvidence.entity_id.in_(entity_ids)).order_by(SourcePassage.ordinal)).scalars().unique().all()
    return [PassageHit(p.id, p.document_id, p.page_number, p.heading, p.normalized_text, 1.0)
            for p in rows if p.id in allowed and not p.injection_flags]


def search_passages(session: Session, adventure_id: uuid.UUID, query: str, allowed: set[uuid.UUID],
                    limit: int = 5) -> list[PassageHit]:
    query = query.strip()[:300]
    if not query or not allowed:
        return []
    tsq = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank(SourcePassage.search_tsv, tsq)
    stmt = (select(SourcePassage, rank.label("rank"))
            .join(SourceDocument, SourceDocument.id == SourcePassage.document_id)
            .where(SourceDocument.adventure_id == adventure_id,
                   SourcePassage.id.in_(allowed),
                   SourcePassage.search_tsv.op("@@")(tsq))
            .order_by(rank.desc()).limit(limit))
    out = []
    for p, r in session.execute(stmt).all():
        if p.injection_flags:
            continue
        out.append(PassageHit(p.id, p.document_id, p.page_number, p.heading, p.normalized_text, float(r)))
    return out


def as_context(hit: PassageHit, kind: str = "passage", title: str | None = None) -> ContextItem:
    return ContextItem(kind=kind, claim_class=ClaimClass.VERIFIED_SOURCE, title=title or hit.heading or "passage",
                       text=hit.text, citations=(hit.ref(),))
