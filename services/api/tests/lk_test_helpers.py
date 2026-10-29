"""Shared helpers for API integration tests."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from lk_api.ingestion.ocr import OcrEngine, StaticOcr
from lk_api.worker import process_available

REJECT_NAMES = {"Wren Haloway"}


class CyclingRoller:
    """Deterministic test roller: cycles through faces, clamping to the die size."""

    kind = "cycling-test-only"

    def __init__(self, faces: list[int]) -> None:
        self.faces, self.i = faces, 0

    def roll_die(self, sides: int) -> int:
        face = self.faces[self.i % len(self.faces)]
        self.i += 1
        return max(1, min(face, sides))


class ScriptedD20Roller:
    """Test roller: d20 faces come from a script (then cycle a tail); other dice always roll max."""

    kind = "scripted-test-only"

    def __init__(self, head: list[int], tail: list[int]) -> None:
        self.head, self.tail, self.i = list(head), tail, 0

    def roll_die(self, sides: int) -> int:
        if sides != 20:
            return sides
        if self.head:
            return self.head.pop(0)
        face = self.tail[self.i % len(self.tail)]
        self.i += 1
        return face


def static_ocr(manifest: dict) -> StaticOcr:
    return StaticOcr({int(k): v for k, v in manifest["ocr_text"].items()})


def upload(client: TestClient, adventure_id: str, pdf: bytes, **form) -> dict:
    data = {"rights_status": "ORIGINAL_WORK", "rights_acknowledged": "true", "document_kind": "ADVENTURE_MODULE"}
    data.update(form)
    r = client.post(f"/api/v1/adventures/{adventure_id}/documents", data=data,
                    files={"file": ("saltmere.pdf", pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()


def run_worker(client: TestClient, ocr: OcrEngine) -> int:
    app = client.app
    return process_available(app.state.db, app.state.storage, app.state.settings, ocr)


def ingest(client: TestClient, pdf: bytes, manifest: dict, *, ocr: OcrEngine | None = None) -> tuple[str, str]:
    adv = client.post("/api/v1/adventures", json={"title": "Saltmere"}).json()
    doc = upload(client, adv["id"], pdf)
    assert run_worker(client, ocr or static_ocr(manifest)) == 1
    return adv["id"], doc["id"]


def approve_all_sound(client: TestClient, adventure_id: str) -> dict[str, list[str]]:
    """Reviewer behaviour for the fixture: approve sound candidates, reject duplicates and broken links."""
    cands = client.get(f"/api/v1/adventures/{adventure_id}/candidates").json()
    result: dict[str, list[str]] = {"approved": [], "rejected": []}
    order = ["LOCATION", "NPC", "MONSTER", "CLUE", "SECRET", "QUEST", "CONNECTION", "EDGE"]
    for kind in order:
        for c in [c for c in cands if c["type"] == kind]:
            involved = {c["name"], *(str(v) for v in c["data"].values())}
            if involved & REJECT_NAMES or any(w["code"] in ("BROKEN_REFERENCE", "DUPLICATE_ENTITY")
                                              for w in c["warnings"]):
                client.post(f"/api/v1/candidates/{c['id']}/reject", json={"reviewer_note": "test review"})
                result["rejected"].append(c["name"])
                continue
            r = client.post(f"/api/v1/candidates/{c['id']}/approve")
            assert r.status_code == 200, (c["name"], r.text)
            result["approved"].append(c["name"])
    return result


CHARACTER = {
    "name": "Ash",
    "class_key": "wayfarer",
    "ability_scores": {"STR": 14, "DEX": 12, "CON": 13, "INT": 10, "WIS": 15, "CHA": 9},
    "starting_items": [{"item_key": "torch", "quantity": 2}, {"item_key": "short_blade", "quantity": 1},
                       {"item_key": "rope", "quantity": 1}],
}


def start_campaign(client: TestClient, adventure_id: str, name: str = "Saltmere run") -> dict:
    c = client.post("/api/v1/campaigns", json={"name": name, "accept_house_rules": True,
                                               "adventure_id": adventure_id}).json()
    r = client.post(f"/api/v1/campaigns/{c['id']}/character", json=CHARACTER)
    assert r.status_code == 201, r.text
    r = client.post(f"/api/v1/campaigns/{c['id']}/start", json={})
    assert r.status_code == 200, r.text
    return {"campaign_id": c["id"], "state": r.json()}


def setup_campaign(client: TestClient, pdf: bytes, manifest: dict) -> dict:
    adventure_id, document_id = ingest(client, pdf, manifest)
    approve_all_sound(client, adventure_id)
    return {**start_campaign(client, adventure_id), "adventure_id": adventure_id, "document_id": document_id}


def act(client: TestClient, campaign_id: str, text: str, version: int | None = None,
        key: str | None = None, expect: int = 200) -> dict:
    if version is None:
        version = client.get(f"/api/v1/campaigns/{campaign_id}").json()["campaign"]["version"]
    r = client.post(f"/api/v1/campaigns/{campaign_id}/actions",
                    json={"input": text, "expected_version": version, "idempotency_key": key or uuid.uuid4().hex})
    assert r.status_code == expect, r.text
    return r.json()


def exit_named(state: dict, name: str) -> dict | None:
    return next((x for x in state["exits"] if x["name"] == name), None)
