"""Adventure library: adventures, private source documents, ingestion status, and candidate review."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from .. import review
from ..errors import ApiError, not_found
from ..ingestion.pdf import PdfRejected, inspect_pdf
from ..ingestion.pipeline import enqueue
from ..models import (
    Adventure,
    AdventureEntity,
    Campaign,
    CampaignStatus,
    CandidateStatus,
    CandidateType,
    DocumentKind,
    DocumentStatus,
    EntityEvidence,
    ExtractionCandidate,
    IngestionJob,
    IngestionWarning,
    JobStatus,
    RightsStatus,
    SourceDocument,
    SourcePage,
    SourcePassage,
    User,
)
from ..security import CurrentUser

router = APIRouter()
log = logging.getLogger("lk_api.library")


def _adventure(s, user: User, adventure_id: uuid.UUID) -> Adventure:
    adv = s.scalar(select(Adventure).where(Adventure.id == adventure_id, Adventure.owner_id == user.id))
    if adv is None:
        raise not_found("Adventure")
    return adv


def _document(s, user: User, document_id: uuid.UUID) -> SourceDocument:
    doc = s.scalar(select(SourceDocument).where(SourceDocument.id == document_id, SourceDocument.owner_id == user.id))
    if doc is None:
        raise not_found("Document")
    return doc


def _doc_dict(s, doc: SourceDocument) -> dict[str, Any]:
    job = s.scalar(select(IngestionJob).where(IngestionJob.document_id == doc.id)
                   .order_by(IngestionJob.created_at.desc()).limit(1))
    return {
        "id": str(doc.id), "adventure_id": str(doc.adventure_id), "display_name": doc.display_name,
        "sha256": doc.sha256, "size_bytes": doc.size_bytes, "page_count": doc.page_count,
        "document_kind": doc.document_kind.value, "rights_status": doc.rights_status.value,
        "rights_note": doc.rights_note, "rights_acknowledged_at": doc.rights_acknowledged_at.isoformat(),
        "visibility": doc.visibility, "allow_remote_ai": doc.allow_remote_ai, "status": doc.status.value,
        "created_at": doc.created_at.isoformat(),
        "job": None if job is None else {
            "id": str(job.id), "status": job.status.value, "attempts": job.attempts, "max_attempts": job.max_attempts,
            "error_code": job.error_code, "error_message": job.error_message, "stats": job.stats},
    }


# ---- adventures -----------------------------------------------------------------


class AdventureCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


@router.post("/adventures", status_code=201)
def create_adventure(body: AdventureCreate, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        adv = Adventure(owner_id=user.id, title=body.title.strip(), description=body.description)
        s.add(adv)
        s.flush()
        return {"id": str(adv.id), "title": adv.title, "description": adv.description}


@router.get("/adventures")
def list_adventures(request: Request, user: User = CurrentUser) -> list[dict]:
    with request.app.state.db.session() as s:
        out = []
        for adv in s.scalars(select(Adventure).where(Adventure.owner_id == user.id).order_by(Adventure.created_at)):
            docs = s.scalars(select(SourceDocument).where(SourceDocument.adventure_id == adv.id)).all()
            counts = dict(s.execute(select(AdventureEntity.entity_type, func.count())
                                    .where(AdventureEntity.adventure_id == adv.id)
                                    .group_by(AdventureEntity.entity_type)).all())
            out.append({"id": str(adv.id), "title": adv.title, "description": adv.description,
                        "graph_version": adv.graph_version, "documents": [_doc_dict(s, d) for d in docs],
                        "entity_counts": {k.value: v for k, v in counts.items()}})
        return out


@router.get("/adventures/{adventure_id}")
def get_adventure(adventure_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        adv = _adventure(s, user, adventure_id)
        docs = s.scalars(select(SourceDocument).where(SourceDocument.adventure_id == adv.id)).all()
        return {"id": str(adv.id), "title": adv.title, "description": adv.description,
                "graph_version": adv.graph_version, "documents": [_doc_dict(s, d) for d in docs]}


@router.get("/adventures/{adventure_id}/graph")
def get_graph(adventure_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        _adventure(s, user, adventure_id)
        return review.adventure_graph(s, adventure_id)


@router.delete("/adventures/{adventure_id}")
def delete_adventure(adventure_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    storage = request.app.state.storage
    with request.app.state.db.session() as s:
        adv = _adventure(s, user, adventure_id)
        if s.scalar(select(Campaign.id).where(Campaign.adventure_id == adv.id).limit(1)):
            raise ApiError(409, "ADVENTURE_IN_USE", "Delete or detach campaigns that use this adventure first")
        keys = list(s.scalars(select(SourceDocument.storage_key).where(SourceDocument.adventure_id == adv.id)))
        s.delete(adv)
    for key in keys:
        storage.delete(key)
        storage.delete_derived_prefix(key)
    return {"deleted": True, "documents_deleted": len(keys)}


# ---- documents --------------------------------------------------------------------

_SAFE_NAME = re.compile(r"[^\w .()\-]+")


@router.post("/adventures/{adventure_id}/documents", status_code=201)
async def upload_document(
    adventure_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    rights_status: RightsStatus = Form(...),
    rights_acknowledged: bool = Form(...),
    document_kind: DocumentKind = Form(DocumentKind.ADVENTURE_MODULE),
    rights_note: str = Form("", max_length=2000),
    user: User = CurrentUser,
) -> dict:
    settings = request.app.state.settings
    if not rights_acknowledged:
        raise ApiError(422, "RIGHTS_NOT_ACKNOWLEDGED",
                       "Confirm you have a legitimate basis to upload and process this document. This "
                       "acknowledgement is recorded; it is not a determination that processing is authorized.")
    limit = settings.max_upload_mb * 1024 * 1024
    chunks, size = [], 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise ApiError(413, "UPLOAD_TOO_LARGE", f"Uploads are limited to {settings.max_upload_mb} MB")
        chunks.append(chunk)
    data = b"".join(chunks)
    try:
        page_count = inspect_pdf(data, max_pages=settings.max_pages)
    except PdfRejected as exc:
        raise ApiError(415 if exc.code == "NOT_A_PDF" else 422, exc.code, exc.message) from exc
    sha = hashlib.sha256(data).hexdigest()
    display = _SAFE_NAME.sub("_", (file.filename or "document.pdf").rsplit("/", 1)[-1].rsplit("\\", 1)[-1])[:200]
    storage = request.app.state.storage
    key = storage.new_key("originals")
    with request.app.state.db.session() as s:
        _adventure(s, user, adventure_id)
        dup = s.scalar(select(SourceDocument.id).where(SourceDocument.owner_id == user.id, SourceDocument.sha256 == sha))
        if dup:
            raise ApiError(409, "DUPLICATE_DOCUMENT", "This exact file was already uploaded", document_id=str(dup))
        storage.write(key, data)
        doc = SourceDocument(owner_id=user.id, adventure_id=adventure_id, display_name=display, storage_key=key,
                             sha256=sha, size_bytes=size, page_count=page_count, media_type="application/pdf",
                             document_kind=document_kind, rights_status=rights_status, rights_note=rights_note,
                             rights_acknowledged_at=datetime.now(UTC), status=DocumentStatus.UPLOADED)
        s.add(doc)
        s.flush()
        enqueue(s, doc.id)
        log.info("document uploaded", extra={"document_id": str(doc.id), "size_bytes": size, "pages": page_count})
        return _doc_dict(s, doc)


@router.get("/documents/{document_id}")
def get_document(document_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        doc = _document(s, user, document_id)
        out = _doc_dict(s, doc)
        pages = s.scalars(select(SourcePage).where(SourcePage.document_id == doc.id).order_by(SourcePage.page_number))
        out["pages"] = [{"page_number": p.page_number, "method": p.extraction_method.value, "chars": p.char_count,
                         "ocr_confidence": p.ocr_confidence} for p in pages]
        warnings = s.scalars(select(IngestionWarning).where(IngestionWarning.document_id == doc.id)
                             .order_by(IngestionWarning.created_at))
        out["warnings"] = [{"id": str(w.id), "code": w.code.value, "severity": w.severity, "message": w.message,
                            "page_number": w.page_number, "candidate_id": str(w.candidate_id) if w.candidate_id else None}
                           for w in warnings]
        return out


@router.get("/documents/{document_id}/passages")
def get_passages(document_id: uuid.UUID, request: Request, page: int | None = None, user: User = CurrentUser) -> list:
    with request.app.state.db.session() as s:
        doc = _document(s, user, document_id)
        stmt = select(SourcePassage).where(SourcePassage.document_id == doc.id)
        if page is not None:
            stmt = stmt.where(SourcePassage.page_number == page)
        return [{"id": str(p.id), "page_number": p.page_number, "ordinal": p.ordinal, "heading": p.heading,
                 "text": p.normalized_text, "raw_text": p.raw_text, "bbox": p.bbox,
                 "method": p.extraction_method.value, "injection_flags": p.injection_flags}
                for p in s.scalars(stmt.order_by(SourcePassage.ordinal))]


@router.post("/documents/{document_id}/reprocess", status_code=202)
def reprocess(document_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        doc = _document(s, user, document_id)
        active = s.scalar(select(IngestionJob.id).where(IngestionJob.document_id == doc.id,
                                                        IngestionJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])))
        if active:
            raise ApiError(409, "JOB_ALREADY_ACTIVE", "An ingestion job is already queued or running")
        job = enqueue(s, doc.id)
        doc.status = DocumentStatus.UPLOADED
        return {"job_id": str(job.id)}


@router.delete("/documents/{document_id}")
def delete_document(document_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    """Delete the original file and every derived artifact, including approved entities it alone supports."""
    storage = request.app.state.storage
    with request.app.state.db.session() as s:
        doc = _document(s, user, document_id)
        in_play = s.scalar(select(Campaign.id).where(Campaign.adventure_id == doc.adventure_id,
                                                     Campaign.status != CampaignStatus.SETUP).limit(1))
        passage_ids = select(SourcePassage.id).where(SourcePassage.document_id == doc.id)
        derived = set(s.scalars(select(EntityEvidence.entity_id).where(EntityEvidence.passage_id.in_(passage_ids))))
        if derived and in_play:
            raise ApiError(409, "DOCUMENT_IN_USE", "A started campaign uses entities derived from this document; "
                                                   "delete that campaign first")
        key = doc.storage_key
        s.delete(doc)
        s.flush()
        orphaned = [e for e in derived if not s.scalar(select(EntityEvidence.passage_id)
                                                       .where(EntityEvidence.entity_id == e).limit(1))]
        if orphaned:
            s.execute(delete(AdventureEntity).where(AdventureEntity.id.in_(orphaned)))
    storage.delete(key)
    storage.delete_derived_prefix(key)
    log.info("document deleted", extra={"document_id": str(document_id), "entities_removed": len(orphaned)})
    return {"deleted": True, "entities_removed": len(orphaned)}


# ---- candidates ---------------------------------------------------------------------


def _cand_dict(s, c: ExtractionCandidate, *, with_text: bool = False) -> dict[str, Any]:
    ids = [e.passage_id for e in c.evidence]
    passages = s.scalars(select(SourcePassage).where(SourcePassage.id.in_(ids)).order_by(SourcePassage.ordinal)).all() if ids else []
    warnings = s.scalars(select(IngestionWarning).where(IngestionWarning.candidate_id == c.id)).all()
    return {
        "id": str(c.id), "type": c.candidate_type.value, "name": c.name, "data": c.data, "status": c.status.value,
        "edited": c.edited, "reviewer_note": c.reviewer_note,
        "evidence": [{"passage_id": str(p.id), "page_number": p.page_number, "ordinal": p.ordinal,
                      **({"text": p.normalized_text} if with_text else {}),
                      "injection_flags": p.injection_flags} for p in passages],
        "warnings": [{"code": w.code.value, "severity": w.severity, "message": w.message} for w in warnings],
        "promoted_entity_id": str(c.promoted_entity_id) if c.promoted_entity_id else None,
    }


@router.get("/adventures/{adventure_id}/candidates")
def list_candidates(adventure_id: uuid.UUID, request: Request, status: CandidateStatus | None = None,
                    type: CandidateType | None = None, user: User = CurrentUser) -> list[dict]:  # noqa: A002
    with request.app.state.db.session() as s:
        _adventure(s, user, adventure_id)
        stmt = select(ExtractionCandidate).where(ExtractionCandidate.adventure_id == adventure_id)
        if status:
            stmt = stmt.where(ExtractionCandidate.status == status)
        if type:
            stmt = stmt.where(ExtractionCandidate.candidate_type == type)
        return [_cand_dict(s, c, with_text=True) for c in s.scalars(stmt.order_by(ExtractionCandidate.created_at))]


class CandidatePatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    data: dict[str, Any] | None = None
    reviewer_note: str | None = Field(default=None, max_length=2000)


class RejectBody(BaseModel):
    reviewer_note: str = Field(default="", max_length=2000)


@router.patch("/candidates/{candidate_id}")
def patch_candidate(candidate_id: uuid.UUID, body: CandidatePatch, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = review.edit_candidate(s, user, candidate_id, name=body.name, data=body.data, note=body.reviewer_note)
        s.flush()
        return _cand_dict(s, c, with_text=True)


@router.post("/candidates/{candidate_id}/approve")
def approve(candidate_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = review.approve_candidate(s, user, candidate_id)
        s.flush()
        return _cand_dict(s, c)


@router.post("/candidates/{candidate_id}/reject")
def reject(candidate_id: uuid.UUID, body: RejectBody, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = review.reject_candidate(s, user, candidate_id, body.reviewer_note)
        s.flush()
        return _cand_dict(s, c)
