"""Private rules profiles: loading, validation, version pinning, explicit upgrade.

All profiles here are synthetic. No values from any published rulebook appear in tests.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from conftest import ROOT, login
from lk_api.profiles import ProfileLoadError, load_profile_dir
from lk_rules import DEV_HOUSE_RULES, RuleEntry, RuleStatus
from lk_test_helpers import CHARACTER, act, approve_all_sound, ingest

KEY = "synthetic-owner"


def synthetic_profile(normal_dc: int, *, version: str = "1.0.0", source: str = "Synthetic Book p.7") -> dict:
    entries = dict(DEV_HOUSE_RULES.entries)
    entries["check.difficulty"] = RuleEntry(status=RuleStatus.VERIFIED, source=source, verified_on=date(2026, 10, 29),
                                            value={"easy": 5, "normal": normal_dc, "hard": 19})
    profile = DEV_HOUSE_RULES.model_copy(update={"key": KEY, "name": "Synthetic owner profile", "version": version,
                                                 "entries": entries, "requires_house_rule_acceptance": False})
    return profile.model_dump(mode="json")


def write_profile(directory: Path, data: dict, name: str = KEY) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.profile.json").write_text(json.dumps(data), encoding="utf-8")


def _client(app_factory, profile_dir: Path) -> TestClient:
    c = TestClient(app_factory(rules_profile_dir=str(profile_dir)))
    c.__enter__()
    c.headers.update(login(c))
    return c


def _climb_target(client, cid) -> str:
    out = act(client, cid, "I try to climb the gatehouse wall")
    return next(line for line in out["outcome"] if "check" in line)


def test_campaign_pins_profile_until_explicit_upgrade(app_factory, tmp_path, fixture_pdf, manifest):
    pdir = tmp_path / "profiles"
    write_profile(pdir, synthetic_profile(13))
    client = _client(app_factory, pdir)
    try:
        profiles = {p["key"]: p for p in client.get("/api/v1/rules/profiles").json()}
        assert profiles[KEY]["private"] is True and not profiles["lk-dev-house-rules"]["private"]
        assert profiles[KEY]["entries"]["check.difficulty"]["status"] == "VERIFIED"
        adventure_id, _ = ingest(client, fixture_pdf, manifest)
        approve_all_sound(client, adventure_id)
        # The synthetic profile still contains house-rule entries, so acceptance is required.
        r = client.post("/api/v1/campaigns", json={"name": "x", "rules_profile": KEY, "adventure_id": adventure_id})
        assert r.json()["error"]["code"] == "HOUSE_RULES_NOT_ACCEPTED"
        cid = client.post("/api/v1/campaigns", json={"name": "Pinned", "rules_profile": KEY, "accept_house_rules": True,
                                                     "adventure_id": adventure_id}).json()["id"]
        assert client.post(f"/api/v1/campaigns/{cid}/character", json=CHARACTER).status_code == 201
        assert client.post(f"/api/v1/campaigns/{cid}/start", json={}).status_code == 200
        assert "target 13" in _climb_target(client, cid)
        first_hash = client.get(f"/api/v1/campaigns/{cid}/rules-profile").json()["pinned"]["content_hash"]
    finally:
        client.__exit__(None, None, None)

    # The owner edits the profile file and restarts. The running campaign must not change.
    write_profile(pdir, synthetic_profile(17, version="1.1.0"))
    client = _client(app_factory, pdir)
    try:
        status = client.get(f"/api/v1/campaigns/{cid}/rules-profile").json()
        assert status["update_available"] is True and status["pinned"]["content_hash"] == first_hash
        assert status["available"]["version"] == "1.1.0"
        assert "target 13" in _climb_target(client, cid)

        up = client.post(f"/api/v1/campaigns/{cid}/rules-profile/upgrade", json={})
        assert up.json()["error"]["code"] == "HOUSE_RULES_NOT_ACCEPTED"
        up = client.post(f"/api/v1/campaigns/{cid}/rules-profile/upgrade", json={"accept_house_rules": True})
        assert up.status_code == 200, up.text
        assert "target 17" in _climb_target(client, cid)
        again = client.post(f"/api/v1/campaigns/{cid}/rules-profile/upgrade", json={"accept_house_rules": True})
        assert again.json()["error"]["code"] == "RULES_PROFILE_CURRENT"
        events = client.get(f"/api/v1/campaigns/{cid}/diagnostics").json()["events"]
        upgraded = next(e for e in events if e["type"] == "RULES_PROFILE_UPGRADED")
        assert upgraded["payload"]["from_hash"] == first_hash
        assert client.get(f"/api/v1/campaigns/{cid}/rules-profile").json()["update_available"] is False
    finally:
        client.__exit__(None, None, None)


def test_invalid_profile_files_stop_startup(app_factory, tmp_path):
    pdir = tmp_path / "bad"
    write_profile(pdir, synthetic_profile(13, source="remembered it"))
    with pytest.raises(ProfileLoadError, match="cite a page"):
        app_factory(rules_profile_dir=str(pdir))
    pdir2 = tmp_path / "clash"
    write_profile(pdir2, {**synthetic_profile(13), "key": "lk-dev-house-rules"}, name="clash")
    with pytest.raises(ProfileLoadError, match="collides"):
        load_profile_dir(pdir2)
    pdir3 = tmp_path / "garbage"
    pdir3.mkdir()
    (pdir3 / "x.profile.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileLoadError, match="not a valid profile"):
        load_profile_dir(pdir3)
    with pytest.raises(ProfileLoadError, match="not a directory"):
        load_profile_dir(tmp_path / "missing")


def test_legacy_campaign_is_pinned_at_startup(app_factory, tmp_path):
    with TestClient(app_factory()) as client:
        client.headers.update(login(client))
        cid = client.post("/api/v1/campaigns", json={"name": "Old", "accept_house_rules": True}).json()["id"]
        with client.app.state.db.engine.begin() as conn:
            conn.execute(sa.text("UPDATE campaign SET rules_profile_version_id = NULL"))
    with TestClient(app_factory()) as client:
        client.headers.update(login(client))
        assert client.get(f"/api/v1/campaigns/{cid}/rules-profile").json()["pinned"] is not None


def test_template_profile_is_valid_and_blocks_everything(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    from profile_template import build

    from lk_rules.coverage import CoverageStatus, coverage

    profile = build("owner-template", "Template")
    write_profile(tmp_path, profile.model_dump(mode="json"), name="owner-template")
    loaded = load_profile_dir(tmp_path)["owner-template"]
    assert all(e.value is None for e in loaded.entries.values())
    statuses = {r.mechanic: r.status for r in coverage(loaded)}
    assert statuses["check.ability"] == CoverageStatus.BLOCKED_UNVERIFIED
