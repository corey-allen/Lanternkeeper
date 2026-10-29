"""Health, authentication, authorization isolation, configuration safety."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lk_api.models import User
from lk_api.security import hash_password
from lk_dm.providers import RemoteTransmissionDisabled
from lk_test_helpers import CHARACTER, ingest
from conftest import login


def test_health_is_public(app_factory):
    with TestClient(app_factory()) as c:
        r = c.get("/api/v1/health")
        assert r.status_code == 200 and r.json()["database"] == "ok"
        assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/campaigns"), ("post", "/api/v1/campaigns"), ("get", "/api/v1/adventures"),
    ("get", "/api/v1/documents/00000000-0000-0000-0000-000000000000"),
    ("get", "/api/v1/campaigns/00000000-0000-0000-0000-000000000000/diagnostics"),
    ("get", "/api/v1/system/ai-disclosure"), ("get", "/api/v1/rules/profiles"),
])
def test_routes_require_authentication(app_factory, method, path):
    with TestClient(app_factory()) as c:
        r = getattr(c, method)(path)
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_bad_login_and_tampered_token(app_factory):
    with TestClient(app_factory()) as c:
        assert c.post("/api/v1/auth/login", json={"username": "keeper", "password": "nope"}).status_code == 401
        headers = login(c)
        token = headers["Authorization"].split()[1]
        tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
        assert c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tampered}"}).status_code == 401
        assert c.get("/api/v1/auth/me", headers=headers).json()["username"] == "keeper"


def test_login_rate_limited(app_factory):
    with TestClient(app_factory()) as c:
        for _ in range(10):
            c.post("/api/v1/auth/login", json={"username": "keeper", "password": "wrong"})
        r = c.post("/api/v1/auth/login", json={"username": "keeper", "password": "wrong"})
        assert r.status_code == 429


def test_cookie_session_is_httponly(app_factory):
    with TestClient(app_factory()) as c:
        r = c.post("/api/v1/auth/login", json={"username": "keeper", "password": "correct-horse-battery-staple"})
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert c.get("/api/v1/auth/me").status_code == 200  # cookie auth works


def test_other_user_cannot_see_or_touch_resources(client, fixture_pdf, manifest):
    adventure_id, document_id = ingest(client, fixture_pdf, manifest)
    camp = client.post("/api/v1/campaigns", json={"name": "Mine", "accept_house_rules": True,
                                                  "adventure_id": adventure_id}).json()
    cand = client.get(f"/api/v1/adventures/{adventure_id}/candidates").json()[0]
    with client.app.state.db.session() as s:
        s.add(User(username="intruder", password_hash=hash_password("intruder-password-123")))
    other = TestClient(client.app)
    other.headers.update(login(other, "intruder", "intruder-password-123"))
    for path in (f"/api/v1/campaigns/{camp['id']}", f"/api/v1/campaigns/{camp['id']}/diagnostics",
                 f"/api/v1/campaigns/{camp['id']}/export", f"/api/v1/documents/{document_id}",
                 f"/api/v1/documents/{document_id}/passages", f"/api/v1/adventures/{adventure_id}/graph",
                 f"/api/v1/adventures/{adventure_id}/candidates"):
        r = other.get(path)
        assert r.status_code == 404, path
    assert other.post(f"/api/v1/candidates/{cand['id']}/approve").status_code == 404
    assert other.delete(f"/api/v1/documents/{document_id}").status_code == 404
    assert other.delete(f"/api/v1/campaigns/{camp['id']}").status_code == 404
    assert other.post(f"/api/v1/campaigns/{camp['id']}/character", json=CHARACTER).status_code == 404
    assert other.get("/api/v1/campaigns").json() == []
    assert client.get(f"/api/v1/documents/{document_id}").status_code == 200


def test_source_files_have_no_download_route(client, fixture_pdf, manifest):
    _, document_id = ingest(client, fixture_pdf, manifest)
    paths = set(client.get("/api/openapi.json").json()["paths"])
    segments = {seg for p in paths for seg in p.split("/")}
    assert not segments & {"download", "file", "files", "original", "raw", "content"}
    body = client.get(f"/api/v1/documents/{document_id}").json()
    assert "storage_key" not in body and body["visibility"] == "PRIVATE"


def test_seeded_dice_refused_outside_tests(settings_factory):
    with pytest.raises(ValueError):
        settings_factory(environment="production")


def test_insecure_roller_refused_outside_tests(settings_factory, fake_provider):
    from lk_api.main import create_app
    from lk_rules.dice import SeededRoller

    with pytest.raises(RuntimeError):
        create_app(settings_factory(environment="development", dice_mode="secure"), provider=fake_provider,
                   roller=SeededRoller(1))


def test_remote_ai_requires_explicit_opt_in(settings_factory):
    from lk_api.main import create_app

    with pytest.raises(RemoteTransmissionDisabled):
        create_app(settings_factory(ai_provider="openai_compatible", ai_base_url="https://models.example.com/v1"))


def test_ai_disclosure_reports_local(client):
    d = client.get("/api/v1/system/ai-disclosure").json()
    assert d["is_remote"] is False and "No content leaves" in d["message"]


def test_short_secret_key_rejected(settings_factory):
    with pytest.raises(ValueError):
        settings_factory(secret_key="short")


def test_house_rules_must_be_accepted(client):
    r = client.post("/api/v1/campaigns", json={"name": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "HOUSE_RULES_NOT_ACCEPTED"
    ok = client.post("/api/v1/campaigns", json={"name": "x", "accept_house_rules": True})
    assert ok.status_code == 201


def test_unverified_profile_blocks_character_creation(client):
    c = client.post("/api/v1/campaigns", json={"name": "SD", "rules_profile": "shadowdark-unverified"}).json()
    r = client.post(f"/api/v1/campaigns/{c['id']}/character", json=CHARACTER)
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNVERIFIED_RULE"


def test_invalid_character_reports_every_issue(client):
    c = client.post("/api/v1/campaigns", json={"name": "x", "accept_house_rules": True}).json()
    bad = {**CHARACTER, "class_key": "paladin",
           "ability_scores": {**CHARACTER["ability_scores"], "STR": 25}}
    r = client.post(f"/api/v1/campaigns/{c['id']}/character", json=bad)
    assert r.status_code == 422
    assert len(r.json()["error"]["details"]["issues"]) == 2


def test_rules_profiles_expose_coverage_but_no_unverified_values(client):
    profiles = {p["key"]: p for p in client.get("/api/v1/rules/profiles").json()}
    sd = profiles["shadowdark-unverified"]
    assert all(e["value"] is None and e["status"] == "UNVERIFIED" for e in sd["entries"].values())
    statuses = {r["mechanic"]: r["status"] for r in profiles["lk-dev-house-rules"]["coverage"]}
    assert statuses["check.ability"] == "AVAILABLE" and statuses["spell.cast"] == "UNSUPPORTED"


def test_bootstrap_user_created_once(app_factory):
    app = app_factory()
    with TestClient(app):
        pass
    with TestClient(app):
        with app.state.db.session() as s:
            assert len(s.scalars(select(User)).all()) == 1
