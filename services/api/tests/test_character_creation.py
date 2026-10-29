"""Server-rolled character creation through the API, on the invented synthetic system."""

from __future__ import annotations

import importlib.util
import json

import sqlalchemy as sa

from fastapi.testclient import TestClient

from conftest import ROOT, login
from lk_test_helpers import CHARACTER, CyclingRoller, act, approve_all_sound, ingest

_spec = importlib.util.spec_from_file_location(
    "synthetic_creation", ROOT / "packages" / "rules_engine" / "tests" / "test_creation.py")
synthetic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(synthetic)


def _client(app_factory, tmp_path, faces=(4,)):
    pdir = tmp_path / "profiles"
    pdir.mkdir()
    (pdir / "synthetic.profile.json").write_text(json.dumps(synthetic.P.model_dump(mode="json")), encoding="utf-8")
    c = TestClient(app_factory(rules_profile_dir=str(pdir), roller=CyclingRoller(list(faces))))
    c.__enter__()
    c.headers.update(login(c))
    return c


def _campaign(client, adventure_id=None):
    return client.post("/api/v1/campaigns", json={"name": "Drafted", "rules_profile": "synthetic",
                                                  "accept_house_rules": True, "adventure_id": adventure_id}).json()["id"]


def test_draft_flow_end_to_end(app_factory, tmp_path, fixture_pdf, manifest):
    client = _client(app_factory, tmp_path)
    try:
        adventure_id, _ = ingest(client, fixture_pdf, manifest)
        approve_all_sound(client, adventure_id)
        cid = _campaign(client, adventure_id)
        base = f"/api/v1/campaigns/{cid}/character"

        r = client.post(base, json=CHARACTER)
        assert r.status_code == 422 and "creation draft" in r.json()["error"]["message"]
        assert client.get(f"{base}/draft").json()["draft"] is None

        view = client.post(f"{base}/draft").json()
        assert view["draft"]["stage"] == "origin"
        assert view["draft"]["base_scores"] == {a: 8 for a in ("STR", "DEX", "CON", "INT", "WIS", "CHA")}  # 2d6 of 4s
        assert view["can_reroll_stats"] is True  # no score reaches the synthetic threshold
        assert {a["key"] for a in view["options"]["ancestries"]} == {"stonekin", "fenfolk"}
        assert client.post(f"{base}/draft").json()["error"]["code"] == "DRAFT_EXISTS"
        view = client.post(f"{base}/draft/reroll-stats").json()
        assert view["draft"]["rerolls_used"] == 1 and view["can_reroll_stats"] is False
        assert client.post(f"{base}/draft/reroll-stats").json()["error"]["code"] == "VALIDATION_FAILED"
        assert client.put(f"{base}/draft/scores", json={"scores": {"STR": 18}}).status_code == 422

        bad = client.put(f"{base}/draft/origin", json={"ancestry_key": "giant", "class_key": "warden"})
        assert bad.status_code == 422
        client.put(f"{base}/draft/origin", json={"ancestry_key": "stonekin", "class_key": "warden"})
        view = client.post(f"{base}/draft/roll").json()
        assert view["draft"]["stage"] == "rolled" and len(view["draft"]["talents"]) == 1
        assert view["preview"]["ready"] is False and view["preview"]["issues"]
        assert client.post(f"{base}/draft/roll").status_code == 422  # cannot roll again

        talent = view["draft"]["talents"][0]
        choices = {"weapon_type": "spear", "stat": "STR", "alternative": 1, "distribute": {"STR": 2}}
        view = client.put(f"{base}/draft/details", json={
            "name": "Bryn", "alignment": "order", "trait_choices": {"stalwart": {"stat": "DEX"}},
            "talent_choices": {"0": choices}}).json()
        assert view["preview"]["ready"] is True, view["preview"]
        assert view["preview"]["build"]["class_key"] == "warden"

        state = client.post(f"{base}/draft/finalize")
        assert state.status_code == 201, state.text
        ch = state.json()["character"]
        assert ch["name"] == "Bryn" and ch["ancestry_key"] == "stonekin" and ch["alignment"] == "order"
        assert any(f["kind"] == "IMMUNITY_SURPRISE" for f in ch["features"])
        assert any(m["source"].startswith("class:warden/stalwart") for m in ch["modifiers"])
        assert ch["gold_gp"] == 12 and ch["title"] == "Gatewatch"
        assert client.get(f"{base}/draft").json()["draft"] is None  # the draft is consumed
        assert client.post(f"{base}/draft").json()["error"]["code"] == "CHARACTER_EXISTS"

        diag = client.get(f"/api/v1/campaigns/{cid}/diagnostics").json()
        created = next(e for e in diag["events"] if e["type"] == "CHARACTER_CREATED")
        assert created["payload"]["base_scores"]["STR"] == 8 and created["payload"]["modifiers"]
        with client.app.state.db.engine.connect() as conn:
            purposes = [r[0] for r in conn.execute(sa.text("SELECT purpose FROM dice_roll WHERE purpose LIKE 'creation:%'"))]
        assert purposes.count("creation:stat:STR") == 2 and "creation:hit_points" in purposes
        assert "creation:starting_gold" in purposes and any(p.startswith("creation:talent") for p in purposes)

        assert client.post(f"/api/v1/campaigns/{cid}/start", json={}).status_code == 200
        out = act(client, cid, "I try to climb the gatehouse wall")
        assert out["status"] == "COMMITTED" and out["dice"]
    finally:
        client.__exit__(None, None, None)


def test_draft_blocked_by_unverified_profile(client):
    cid = client.post("/api/v1/campaigns", json={"name": "SD", "rules_profile": "shadowdark-unverified"}).json()["id"]
    r = client.post(f"/api/v1/campaigns/{cid}/character/draft")
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNVERIFIED_RULE"


def test_manual_profile_draft_uses_entered_scores(client):
    cid = client.post("/api/v1/campaigns", json={"name": "Dev", "accept_house_rules": True}).json()["id"]
    base = f"/api/v1/campaigns/{cid}/character"
    view = client.post(f"{base}/draft").json()
    assert view["draft"]["stage"] == "stats" and view["draft"]["base_scores"] is None
    view = client.put(f"{base}/draft/scores", json={"scores": CHARACTER["ability_scores"]}).json()
    client.put(f"{base}/draft/origin", json={"class_key": "wayfarer"})
    view = client.post(f"{base}/draft/roll").json()
    assert view["draft"]["hp_roll_total"] == 8  # max hit die, nothing rolled
    client.put(f"{base}/draft/details", json={"name": "Ash"})
    state = client.post(f"{base}/draft/finalize").json()
    assert state["character"]["hp_max"] == 9


def _synthetic_character(client, adventure_id):
    cid = _campaign(client, adventure_id)
    base = f"/api/v1/campaigns/{cid}/character"
    client.post(f"{base}/draft")
    client.put(f"{base}/draft/origin", json={"ancestry_key": "stonekin", "class_key": "warden"})
    client.post(f"{base}/draft/roll")
    client.put(f"{base}/draft/details", json={
        "name": "Bryn", "alignment": "order", "trait_choices": {"stalwart": {"stat": "STR"}},
        "talent_choices": {"0": {"weapon_type": "spear", "stat": "STR", "alternative": 1, "distribute": {"STR": 2}}}})
    assert client.post(f"{base}/draft/finalize").status_code == 201
    assert client.post(f"/api/v1/campaigns/{cid}/start", json={}).status_code == 200
    return cid


def test_checks_apply_character_features_and_modifiers(app_factory, tmp_path, fixture_pdf, manifest):
    from lk_dm.providers import FakeProvider

    client = _client(app_factory, tmp_path)
    try:
        adventure_id, _ = ingest(client, fixture_pdf, manifest)
        approve_all_sound(client, adventure_id)
        cid = _synthetic_character(client, adventure_id)

        tracked = act(client, cid, "I try to track the footprints in the mud")
        assert tracked["status"] == "COMMITTED" and tracked["dice"] == []
        assert "no roll needed" in tracked["outcome"][0] and "Trailwise" in tracked["outcome"][0]

        forced = act(client, cid, "I try to force the rusted gate open")
        roll = forced["dice"][0]
        assert roll["expression"] == "2d20kh1"  # Stalwart: advantage on STR checks to force things
        assert "Stalwart" in forced["outcome"][0]

        climbed = act(client, cid, "I try to climb the gatehouse wall")
        assert climbed["dice"][0]["expression"] == "d20"  # no climbing modifiers

        # A model may only use the profile's task categories.
        provider = FakeProvider(script=[{"intent": "ability_check", "ability": "STR", "difficulty": "normal",
                                         "task_tag": "juggling"}])
        client.app.state.services.provider = provider
        bad = act(client, cid, "I juggle")
        assert bad["status"] == "REJECTED" and bad["error"]["code"] == "INVALID_TASK"
    finally:
        client.__exit__(None, None, None)
