"""Retryable, idempotent ingestion job.

A run replaces ALL derived rows for the document inside one transaction, so a
retry after any failure starts clean and never duplicates pages, passages, or
candidates. Re-ingesting a document that already has approved entities is
refused, because it would destroy their provenance.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from lk_dm.safety import detect_prompt_injection

from ..db import Database
from ..models import (
    CandidateEvidence,
    CandidateStatus,
    DocumentStatus,
    ExtractionCandidate,
    ExtractionMethod,
    IngestionJob,
    IngestionWarning,
    JobStatus,
    SourceDocument,
    SourcePage,
    SourcePassage,
    WarningCode,
)
from ..storage import FileStorage
from .extractor import HeuristicExtractor
from .ocr import OcrEngine, OcrUnavailable
from .pdf import PageExtraction, extract_pages
from .text import blocks_from_ocr_text, normalize, normalize_key, split_passages
from .validation import validate_drafts

log = logging.getLogger("lk_api.ingestion")

STALE_LOCK = timedelta(minutes=15)


class IngestionFailure(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


def enqueue(session: Session, document_id: uuid.UUID, extractor: str = "heuristic") -> IngestionJob:
    job = IngestionJob(document_id=document_id, status=JobStatus.QUEUED, extractor=extractor)
    session.add(job)
    session.flush()
    return job


def claim_next_job(db: Database) -> uuid.UUID | None:
    """Claim one queued job (or a stale running one) using SKIP LOCKED."""
    now = datetime.now(UTC)
    with db.session() as s:
        s.execute(update(IngestionJob)
                  .where(IngestionJob.status == JobStatus.RUNNING, IngestionJob.locked_at < now - STALE_LOCK)
                  .values(status=JobStatus.QUEUED, locked_at=None))
        job = s.scalar(select(IngestionJob).where(IngestionJob.status == JobStatus.QUEUED)
                       .order_by(IngestionJob.created_at).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        job.status = JobStatus.RUNNING
        job.attempts += 1
        job.locked_at = now
        job.started_at = now
        doc = s.get(SourceDocument, job.document_id)
        if doc:
            doc.status = DocumentStatus.PROCESSING
        return job.id


def _ocr_pages(data: bytes, pages: list[PageExtraction], ocr: OcrEngine, min_chars: int):
    results = []  # (page, method, blocks, confidence, warnings)
    for page in pages:
        warnings: list[tuple[WarningCode, str, str]] = []
        if len(page.text.strip()) >= min_chars:
            results.append((page, ExtractionMethod.TEXT, page.blocks, None, warnings))
            continue
        try:
            if not ocr.available():
                raise OcrUnavailable(f"OCR engine '{ocr.name}' unavailable")
            out = ocr.ocr_page(data, page.page_number)
            blocks = blocks_from_ocr_text(out.text)
            warnings.append((WarningCode.OCR_USED, "info",
                             f"Page {page.page_number} had little embedded text; OCR ({ocr.name}) was used"))
            if len(out.text.strip()) < min_chars:
                warnings.append((WarningCode.LOW_TEXT, "warning",
                                 f"Page {page.page_number}: OCR produced little text"))
            results.append((page, ExtractionMethod.OCR, blocks, out.confidence, warnings))
        except OcrUnavailable:
            warnings.append((WarningCode.OCR_UNAVAILABLE, "warning",
                             f"Page {page.page_number} appears scanned but OCR is unavailable; "
                             "its content was not extracted"))
            method = ExtractionMethod.TEXT if page.blocks else ExtractionMethod.NONE
            results.append((page, method, page.blocks, None, warnings))
    return results


def run_job(db: Database, storage: FileStorage, job_id: uuid.UUID, *, ocr: OcrEngine,
            max_pages: int, ocr_min_chars: int) -> JobStatus:
    try:
        _run(db, storage, job_id, ocr=ocr, max_pages=max_pages, ocr_min_chars=ocr_min_chars)
        return JobStatus.SUCCEEDED
    except Exception as exc:  # noqa: BLE001 - recorded on the job, never with document text
        code = exc.code if isinstance(exc, IngestionFailure) else type(exc).__name__
        message = exc.message if isinstance(exc, IngestionFailure) else "Unexpected ingestion error"
        retryable = exc.retryable if isinstance(exc, IngestionFailure) else True
        log.warning("ingestion failed", extra={"job_id": str(job_id), "error_code": code})
        with db.session() as s:
            job = s.get(IngestionJob, job_id)
            if job is None:
                return JobStatus.FAILED
            job.error_code, job.error_message = code[:64], message[:500]
            job.locked_at = None
            doc = s.get(SourceDocument, job.document_id)
            if retryable and job.attempts < job.max_attempts:
                job.status = JobStatus.QUEUED
            else:
                job.status = JobStatus.FAILED
                job.finished_at = datetime.now(UTC)
                if doc:
                    doc.status = DocumentStatus.FAILED
            return job.status


def _run(db: Database, storage: FileStorage, job_id: uuid.UUID, *, ocr: OcrEngine,
         max_pages: int, ocr_min_chars: int) -> None:
    with db.session() as s:
        job = s.get(IngestionJob, job_id)
        if job is None or job.status != JobStatus.RUNNING:
            raise IngestionFailure("JOB_NOT_RUNNING", "Job is not in RUNNING state", retryable=False)
        doc = s.get(SourceDocument, job.document_id)
        if doc is None:
            raise IngestionFailure("DOCUMENT_MISSING", "Document no longer exists", retryable=False)
        doc_id, adventure_id, key, sha = doc.id, doc.adventure_id, doc.storage_key, doc.sha256

    data = storage.read(key)
    if hashlib.sha256(data).hexdigest() != sha:
        raise IngestionFailure("HASH_MISMATCH", "Stored file does not match its recorded hash", retryable=False)

    pages = extract_pages(data, max_pages=max_pages)
    processed = _ocr_pages(data, pages, ocr, ocr_min_chars)
    drafts_passages = split_passages([(p, m.value, blocks) for p, m, blocks, _, _ in processed])
    for d in drafts_passages:
        d.injection_flags = detect_prompt_injection(d.normalized_text)
    candidates = HeuristicExtractor().extract(drafts_passages)
    warnings, uncertain = validate_drafts(candidates)

    with db.session() as s:
        job = s.get(IngestionJob, job_id, with_for_update=True)
        if job is None or job.status != JobStatus.RUNNING:
            raise IngestionFailure("JOB_NOT_RUNNING", "Job state changed during processing", retryable=False)
        approved = s.scalar(select(ExtractionCandidate.id).where(
            ExtractionCandidate.document_id == doc_id,
            ExtractionCandidate.status == CandidateStatus.APPROVED).limit(1))
        if approved:
            raise IngestionFailure("DOCUMENT_HAS_APPROVED_ENTITIES",
                                   "Re-ingestion would discard provenance of approved entities", retryable=False)
        # Idempotency: wipe everything derived from this document by earlier runs.
        s.execute(delete(IngestionWarning).where(IngestionWarning.document_id == doc_id))
        s.execute(delete(ExtractionCandidate).where(ExtractionCandidate.document_id == doc_id))
        s.execute(delete(SourcePassage).where(SourcePassage.document_id == doc_id))
        s.execute(delete(SourcePage).where(SourcePage.document_id == doc_id))
        s.flush()

        page_ids: dict[int, uuid.UUID] = {}
        for page, method, blocks, confidence, page_warnings in processed:
            raw = "\n\n".join(b.text for b in blocks)
            row = SourcePage(document_id=doc_id, page_number=page.page_number, extraction_method=method,
                             raw_text=raw, normalized_text=normalize(raw), char_count=len(raw),
                             ocr_confidence=confidence, width=page.width, height=page.height)
            s.add(row)
            s.flush()
            page_ids[page.page_number] = row.id
            for code, severity, message in page_warnings:
                s.add(IngestionWarning(job_id=job_id, document_id=doc_id, code=code, severity=severity,
                                       message=message, page_number=page.page_number))

        passage_ids: dict[int, uuid.UUID] = {}
        for d in drafts_passages:
            row = SourcePassage(document_id=doc_id, page_id=page_ids[d.page_number], page_number=d.page_number,
                                ordinal=d.ordinal, heading=d.heading, raw_text=d.raw_text,
                                normalized_text=d.normalized_text, bbox=d.bbox,
                                extraction_method=ExtractionMethod(d.extraction_method),
                                injection_flags=d.injection_flags)
            s.add(row)
            s.flush()
            passage_ids[d.ordinal] = row.id
            if d.injection_flags:
                s.add(IngestionWarning(
                    job_id=job_id, document_id=doc_id, passage_id=row.id, page_number=d.page_number,
                    code=WarningCode.PROMPT_INJECTION_SUSPECTED, severity="warning",
                    message=f"Passage on page {d.page_number} contains instruction-like text; it is "
                            "excluded from model context and was not merged into any entity"))

        cand_ids: list[uuid.UUID] = []
        for i, c in enumerate(candidates):
            row = ExtractionCandidate(
                job_id=job_id, document_id=doc_id, adventure_id=adventure_id, candidate_type=c.candidate_type,
                name=c.name, normalized_name=normalize_key(c.name)[:200], data=c.data,
                status=CandidateStatus.UNCERTAIN if i in uncertain else CandidateStatus.PENDING)
            row.evidence = [CandidateEvidence(passage_id=passage_ids[o]) for o in dict.fromkeys(c.evidence)
                            if o in passage_ids]
            s.add(row)
            s.flush()
            cand_ids.append(row.id)
        for w in warnings:
            s.add(IngestionWarning(job_id=job_id, document_id=doc_id, code=w.code, severity=w.severity,
                                   message=w.message[:500],
                                   candidate_id=cand_ids[w.draft_index] if w.draft_index is not None else None))

        job.status = JobStatus.SUCCEEDED
        job.finished_at = datetime.now(UTC)
        job.locked_at = None
        job.error_code = job.error_message = None
        job.stats = {"pages": len(processed), "passages": len(drafts_passages), "candidates": len(candidates),
                     "warnings": len(warnings), "ocr_pages": sum(1 for _, m, *_ in processed
                                                                 if m == ExtractionMethod.OCR)}
        doc = s.get(SourceDocument, doc_id)
        if doc:
            doc.status = DocumentStatus.READY_FOR_REVIEW
            doc.page_count = len(pages)
    log.info("ingestion succeeded", extra={"job_id": str(job_id), "candidates": len(candidates)})
