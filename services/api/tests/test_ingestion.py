"""Upload validation, extraction, OCR fallback, provenance, warnings, retries, review, deletion."""

from __future__ import annotations

import shutil

import pytest
from sqlalchemy import func, select, update

from lk_api.ingestion import pipeline
from lk_api.ingestion.ocr import NoOcr, TesseractOcr
from lk_api.models import (
    AdventureEntity,
    ExtractionCandidate,
    IngestionJob,
    JobStatus,
    SourceDocument,
    SourcePage,
    SourcePassage,
)
from lk_test_helpers import approve_all_sound, ingest, run_worker, static_ocr, upload


def _warnings(client, document_id):
    return client.get(f"/api/v1/documents/{document_id}").json()["warnings"]


def test_upload_validation(client, fixture_pdf):
    adv = client.post("/api/v1/adventures", json={"title": "A"}).json()["id"]
    url = f"/api/v1/adventures/{adv}/documents"
    form = {"rights_status": "ORIGINAL_WORK", "rights_acknowledged": "true"}
    r = client.post(url, data=form, files={"file": ("x.pdf", b"not a pdf at all", "application/pdf")})
    assert r.status_code == 415 and r.json()["error"]["code"] == "NOT_A_PDF"
    r = client.post(url, data={**form, "rights_acknowledged": "false"}, files={"file": ("x.pdf", fixture_pdf)})
    assert r.json()["error"]["code"] == "RIGHTS_NOT_ACKNOWLEDGED"
    r = client.post(url, data=form, files={"file": ("x.pdf", b"%PDF-1.7 garbage", "application/pdf")})
    assert r.json()["error"]["code"] == "PDF_UNREADABLE"
    first = upload(client, adv, fixture_pdf)
    assert first["visibility"] == "PRIVATE" and first["rights_status"] == "ORIGINAL_WORK"
    assert len(first["sha256"]) == 64 and first["page_count"] == 3
    r = client.post(url, data=form, files={"file": ("again.pdf", fixture_pdf)})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_DOCUMENT"


def test_upload_size_and_page_limits(app_factory, fixture_pdf):
    from fastapi.testclient import TestClient

    from conftest import login

    with TestClient(app_factory(max_pages=2)) as c:
        c.headers.update(login(c))
        adv = c.post("/api/v1/adventures", json={"title": "A"}).json()["id"]
        r = c.post(f"/api/v1/adventures/{adv}/documents",
                   data={"rights_status": "UNKNOWN", "rights_acknowledged": "true"}, files={"file": ("x.pdf", fixture_pdf)})
        assert r.json()["error"]["code"] == "PDF_TOO_MANY_PAGES"
    with TestClient(app_factory(max_upload_mb=0)) as c:
        c.headers.update(login(c))
        adv = c.post("/api/v1/adventures", json={"title": "A"}).json()["id"]
        r = c.post(f"/api/v1/adventures/{adv}/documents",
                   data={"rights_status": "UNKNOWN", "rights_acknowledged": "true"}, files={"file": ("x.pdf", fixture_pdf)})
        assert r.status_code == 413


def test_original_stored_privately_with_generated_key(client, fixture_pdf, manifest):
    _, document_id = ingest(client, fixture_pdf, manifest)
    with client.app.state.db.session() as s:
        doc = s.get(SourceDocument, __import__("uuid").UUID(document_id))
        assert "saltmere" not in doc.storage_key and doc.storage_key.startswith("originals/")
        assert client.app.state.storage.read(doc.storage_key) == fixture_pdf


def test_extraction_pages_passages_and_provenance(client, fixture_pdf, manifest):
    adventure_id, document_id = ingest(client, fixture_pdf, manifest)
    doc = client.get(f"/api/v1/documents/{document_id}").json()
    assert doc["status"] == "READY_FOR_REVIEW" and doc["job"]["status"] == "SUCCEEDED"
    assert [p["method"] for p in doc["pages"]] == ["TEXT", "TEXT", "OCR"]
    passages = client.get(f"/api/v1/documents/{document_id}/passages").json()
    gate = next(p for p in passages if p["text"].startswith("A squat stone gatehouse"))
    assert gate["page_number"] == 1 and gate["heading"] == "AREA 1: The Lantern Gate"
    assert gate["bbox"] and len(gate["bbox"]) == 4 and gate["raw_text"]
    loft = next(p for p in passages if "verdigris" in p["text"])
    assert loft["page_number"] == 3 and loft["method"] == "OCR" and loft["bbox"] is None

    cands = client.get(f"/api/v1/adventures/{adventure_id}/candidates").json()
    by_type: dict[str, set[str]] = {}
    for c in cands:
        by_type.setdefault(c["type"], set()).add(c["name"])
        assert c["evidence"], f"{c['name']} has no evidence"
        assert all(e["page_number"] in (1, 2, 3) for e in c["evidence"])
    exp = manifest["expected"]
    assert set(exp["locations"]) <= by_type["LOCATION"]
    assert set(exp["npcs"]) <= by_type["NPC"]
    assert set(exp["clues"]) <= by_type["CLUE"] and set(exp["secrets"]) <= by_type["SECRET"]
    assert set(exp["monsters"]) <= by_type["MONSTER"] and set(exp["quests"]) <= by_type["QUEST"]
    assert any(c["data"].get("to_location") == "The Drowned Chapel" for c in cands if c["type"] == "CONNECTION")
    wight = next(c for c in cands if c["name"] == "Brine Wight")
    assert wight["data"]["armor_class"] == 11 and wight["data"]["damage"] == "1d4"


def test_warnings_for_duplicates_conflicts_broken_refs_and_injection(client, fixture_pdf, manifest):
    adventure_id, document_id = ingest(client, fixture_pdf, manifest)
    codes = [w["code"] for w in _warnings(client, document_id)]
    msgs = " ".join(w["message"] for w in _warnings(client, document_id))
    assert "DUPLICATE_ENTITY" in codes and "Wren Haloway" in msgs
    assert "CONFLICTING_FACT" in codes and "disposition" in msgs
    assert "BROKEN_REFERENCE" in codes and "The Salt Cellar" in msgs
    assert "PROMPT_INJECTION_SUSPECTED" in codes and "OCR_USED" in codes
    cands = client.get(f"/api/v1/adventures/{adventure_id}/candidates").json()
    assert next(c for c in cands if c["name"] == "Wren Haloway")["status"] == "UNCERTAIN"
    # The injected instruction never became part of any candidate.
    assert not any("Ignore all previous" in str(c["data"]) for c in cands)
    flagged = [p for p in client.get(f"/api/v1/documents/{document_id}/passages").json() if p["injection_flags"]]
    assert len(flagged) == 1 and flagged[0]["page_number"] == 2


def test_ocr_unavailable_is_reported_not_hidden(client, fixture_pdf, manifest):
    adventure_id, document_id = ingest(client, fixture_pdf, manifest, ocr=NoOcr())
    doc = client.get(f"/api/v1/documents/{document_id}").json()
    assert doc["pages"][2]["method"] == "NONE"
    codes = [w["code"] for w in doc["warnings"]]
    assert "OCR_UNAVAILABLE" in codes
    # Without OCR the Bell Loft is missing, so the hidden exit is now a broken reference.
    assert any("The Bell Loft" in w["message"] for w in doc["warnings"] if w["code"] == "BROKEN_REFERENCE")


@pytest.mark.tesseract
@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract OCR is not installed")
def test_real_tesseract_ocr(client, fixture_pdf, manifest):
    _, document_id = ingest(client, fixture_pdf, manifest, ocr=TesseractOcr())
    passages = client.get(f"/api/v1/documents/{document_id}/passages?page=3").json()
    assert any("bell" in p["text"].lower() for p in passages)


def test_rerun_is_idempotent(client, fixture_pdf, manifest):
    adventure_id, document_id = ingest(client, fixture_pdf, manifest)

    def counts():
        with client.app.state.db.session() as s:
            doc_id = __import__("uuid").UUID(document_id)
            return tuple(s.scalar(select(func.count()).select_from(m).where(m.document_id == doc_id))
                         for m in (SourcePage, SourcePassage, ExtractionCandidate))

    before = counts()
    assert client.post(f"/api/v1/documents/{document_id}/reprocess").status_code == 202
    assert run_worker(client, static_ocr(manifest)) == 1
    assert counts() == before


def test_transient_failure_is_retried(client, fixture_pdf, manifest, monkeypatch):
    adv = client.post("/api/v1/adventures", json={"title": "A"}).json()["id"]
    doc = upload(client, adv, fixture_pdf)
    real = pipeline.extract_pages
    calls = {"n": 0}

    def flaky(data, *, max_pages):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk hiccup")
        return real(data, max_pages=max_pages)

    monkeypatch.setattr(pipeline, "extract_pages", flaky)
    assert run_worker(client, static_ocr(manifest)) == 2  # failed once, re-queued, then succeeded
    body = client.get(f"/api/v1/documents/{doc['id']}").json()
    assert body["job"]["status"] == "SUCCEEDED" and body["job"]["attempts"] == 2


def test_integrity_failure_is_not_retried(client, fixture_pdf, manifest):
    adv = client.post("/api/v1/adventures", json={"title": "A"}).json()["id"]
    doc = upload(client, adv, fixture_pdf)
    with client.app.state.db.session() as s:
        s.execute(update(SourceDocument).values(sha256="0" * 64))
    run_worker(client, static_ocr(manifest))
    body = client.get(f"/api/v1/documents/{doc['id']}").json()
    assert body["job"]["status"] == "FAILED" and body["job"]["error_code"] == "HASH_MISMATCH"
    assert body["job"]["attempts"] == 1 and body["status"] == "FAILED"


def test_stale_running_job_is_recovered(client, fixture_pdf, manifest):
    adv = client.post("/api/v1/adventures", json={"title": "A"}).json()["id"]
    doc = upload(client, adv, fixture_pdf)
    from datetime import UTC, datetime, timedelta

    with client.app.state.db.session() as s:
        s.execute(update(IngestionJob).values(status=JobStatus.RUNNING, attempts=1,
                                              locked_at=datetime.now(UTC) - timedelta(hours=1)))
    assert run_worker(client, static_ocr(manifest)) == 1
    assert client.get(f"/api/v1/documents/{doc['id']}").json()["job"]["status"] == "SUCCEEDED"


def test_review_edit_approve_reject(client, fixture_pdf, manifest):
    adventure_id, _ = ingest(client, fixture_pdf, manifest)
    cands = {c["name"]: c for c in client.get(f"/api/v1/adventures/{adventure_id}/candidates").json()}
    conn = cands["The Lantern Gate -> The Drowned Chapel"]
    r = client.post(f"/api/v1/candidates/{conn['id']}/approve")
    assert r.status_code == 409 and r.json()["error"]["code"] == "UNRESOLVED_REFERENCE"
    bad = client.patch(f"/api/v1/candidates/{cands['Brine Wight']['id']}", json={"data": {"damage": "lots"}})
    assert bad.status_code == 422
    edited = client.patch(f"/api/v1/candidates/{cands['Brother Osric']['id']}",
                          json={"data": {"motivation": "Finish the drowned hymn."}, "reviewer_note": "clarified"})
    assert edited.status_code == 200 and edited.json()["edited"] and edited.json()["evidence"]
    assert client.post(f"/api/v1/candidates/{cands['Brother Osric']['id']}/approve").status_code == 200
    assert client.post(f"/api/v1/candidates/{cands['Brother Osric']['id']}/approve").status_code == 409
    assert client.post(f"/api/v1/candidates/{cands['Wren Haloway']['id']}/reject", json={}).json()["status"] == "REJECTED"
    assert client.post(f"/api/v1/candidates/{cands['Wren Haloway']['id']}/approve").status_code == 409
    graph = client.get(f"/api/v1/adventures/{adventure_id}/graph").json()
    osric = next(e for e in graph["entities"] if e["name"] == "Brother Osric")
    assert osric["fields"]["motivation"] == "Finish the drowned hymn." and osric["citations"][0]["page_number"] == 2


def test_nothing_is_authoritative_until_approved(client, fixture_pdf, manifest):
    adventure_id, _ = ingest(client, fixture_pdf, manifest)
    assert client.get(f"/api/v1/adventures/{adventure_id}/graph").json()["entities"] == []
    result = approve_all_sound(client, adventure_id)
    graph = client.get(f"/api/v1/adventures/{adventure_id}/graph").json()
    names = {e["name"] for e in graph["entities"]}
    assert {"The Lantern Gate", "The Drowned Chapel", "Wren Halloway", "Tide-marked hymnal"} <= names
    assert "Wren Haloway" not in names and "Wren Haloway" in result["rejected"]
    assert len(graph["connections"]) == 2 and all(e["citations"] for e in graph["entities"])
    assert {c["to_name"] for c in graph["connections"]} >= {"The Drowned Chapel", "The Bell Loft"}


def test_reingest_after_approval_refused(client, fixture_pdf, manifest):
    adventure_id, document_id = ingest(client, fixture_pdf, manifest)
    approve_all_sound(client, adventure_id)
    client.post(f"/api/v1/documents/{document_id}/reprocess")
    run_worker(client, static_ocr(manifest))
    job = client.get(f"/api/v1/documents/{document_id}").json()["job"]
    assert job["status"] == "FAILED" and job["error_code"] == "DOCUMENT_HAS_APPROVED_ENTITIES"


def test_document_deletion_removes_original_and_derived(client, fixture_pdf, manifest):
    adventure_id, document_id = ingest(client, fixture_pdf, manifest)
    approve_all_sound(client, adventure_id)
    with client.app.state.db.session() as s:
        key = s.get(SourceDocument, __import__("uuid").UUID(document_id)).storage_key
    assert client.app.state.storage.exists(key)
    r = client.delete(f"/api/v1/documents/{document_id}")
    assert r.status_code == 200 and r.json()["entities_removed"] > 0
    assert not client.app.state.storage.exists(key)
    with client.app.state.db.session() as s:
        for model in (SourcePage, SourcePassage, ExtractionCandidate):
            assert s.scalar(select(func.count()).select_from(model)) == 0
        assert s.scalar(select(func.count()).select_from(AdventureEntity)) == 0
    assert client.get(f"/api/v1/documents/{document_id}").status_code == 404


def test_deletion_blocked_while_campaign_uses_document(client, fixture_pdf, manifest):
    from lk_test_helpers import setup_campaign

    ids = setup_campaign(client, fixture_pdf, manifest)
    r = client.delete(f"/api/v1/documents/{ids['document_id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "DOCUMENT_IN_USE"
    assert client.delete(f"/api/v1/campaigns/{ids['campaign_id']}").status_code == 200
    assert client.delete(f"/api/v1/documents/{ids['document_id']}").status_code == 200
