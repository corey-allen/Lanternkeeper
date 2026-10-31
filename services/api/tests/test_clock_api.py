"""Real-time light, timing modes, multiple lights, fuel and darkness through the API (ADR-0013).

Uses a private copy of the dev house rules with light timing added (synthetic values only).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import login
from lk_rules import DEV_HOUSE_RULES, RuleEntry, RuleStatus
from lk_test_helpers import CHARACTER, CyclingRoller, act, approve_all_sound, ingest


def _timed_profile() -> dict:
    entries = dict(DEV_HOUSE_RULES.entries)

    def house(value):
        return RuleEntry(status=RuleStatus.HOUSE_RULE, source="house rule: synthetic test value", value=value)

    entries["time.light_timing"] = house({"modes": ["real_time", "rounds"], "rounds_per_hour": 10,
                                          "multiple_sources": ["ride_along", "restart"]})
    entries["vision.total_darkness"] = house({"sight_checks": "disadvantage"})
    entries["items.catalog"] = house([
        *DEV_HOUSE_RULES.entries["items.catalog"].value,
        {"key": "lamp", "name": "Lamp", "category": "light", "light": {"duration_minutes": 45, "fuel_item_key": "lamp_oil"}},
        {"key": "lamp_oil", "name": "Lamp oil", "consumable": True}])
    profile = DEV_HOUSE_RULES.model_copy(update={"key": "timed-dev", "name": "Timed dev rules", "entries": entries})
    return profile.model_dump(mode="json")


class Now:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t

    def wait(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


@pytest.fixture
def now(monkeypatch) -> Now:
    clock = Now()
    monkeypatch.setattr("lk_api.routes.clock.utcnow", clock)
    monkeypatch.setattr("lk_api.game.orchestrator.utcnow", clock)
    return clock


@pytest.fixture
def timed(app_factory, tmp_path, fixture_pdf, manifest):
    pdir = tmp_path / "profiles"
    pdir.mkdir()
    (pdir / "timed.profile.json").write_text(json.dumps(_timed_profile()), encoding="utf-8")
    with TestClient(app_factory(rules_profile_dir=str(pdir), roller=CyclingRoller([15, 3]))) as client:
        client.headers.update(login(client))
        adventure_id, _ = ingest(client, fixture_pdf, manifest)
        approve_all_sound(client, adventure_id)

        def start(items=None) -> str:
            c = client.post("/api/v1/campaigns", json={"name": "Timed", "rules_profile": "timed-dev",
                                                       "accept_house_rules": True, "adventure_id": adventure_id}).json()
            spec = {**CHARACTER, **({"starting_items": items} if items is not None else {})}
            assert client.post(f"/api/v1/campaigns/{c['id']}/character", json=spec).status_code == 201
            assert client.post(f"/api/v1/campaigns/{c['id']}/start", json={}).status_code == 200
            return c["id"]

        yield client, start


def beat(client, cid, path="heartbeat") -> dict:
    r = client.post(f"/api/v1/campaigns/{cid}/clock/{path}")
    assert r.status_code == 200, r.text
    return r.json()


def view(client, cid) -> dict:
    return client.get(f"/api/v1/campaigns/{cid}").json()


def test_real_time_burns_light_only_while_active(timed, now):
    client, start = timed
    cid = start()
    lit = act(client, cid, "I light a torch")
    clock = lit["state"]["clock"]
    assert clock["timing"] == "real_time" and clock["multiple_lights"] == "ride_along"
    assert clock["lights"][0]["remaining_minutes"] == 60  # lighting takes no game minutes in real time
    assert clock["running"] is False  # paused while the action was processed

    assert beat(client, cid)["clock"]["pending_seconds"] == 0  # resumes; nothing credited yet
    now.wait(30)
    c = beat(client, cid)["clock"]
    assert c["pending_seconds"] == 30 and c["lights"][0]["remaining_seconds"] == 3570 and c["running"]
    now.wait(400)  # longer than the idle pause: the app was closed or idle
    assert beat(client, cid)["clock"]["pending_seconds"] == 30
    now.wait(240)
    out = beat(client, cid)
    assert out["clock"]["pending_seconds"] == 270 and out["changed"] is False  # bookkeeping only, no event

    now.wait(30)  # the action is received 30 s later; model time afterwards is not charged
    moved = act(client, cid, "I walk north to the drowned chapel")
    events = moved["events"]
    assert any(e["type"] == "TIME_ADVANCED" and "real time" in e["summary"] for e in events)
    clock = moved["state"]["clock"]
    assert clock["minutes_elapsed"] == 5 and clock["pending_seconds"] == 0
    assert clock["lights"][0]["remaining_minutes"] == 55 and clock["running"] is False


def test_heartbeat_flushes_when_the_light_burns_out(timed, now):
    client, start = timed
    cid = start()
    act(client, cid, "I light a torch")
    beat(client, cid)
    version = view(client, cid)["campaign"]["version"]
    for _ in range(11):
        now.wait(300)
        assert beat(client, cid)["changed"] is False
    now.wait(300)
    out = beat(client, cid)
    assert out["changed"] is True and out["campaign_version"] == version + 1
    assert "Your torch burned out." in out["outcome"]
    assert out["clock"]["is_lit"] is False and out["clock"]["lights"] == [] and out["clock"]["minutes_elapsed"] == 60
    # Pausing (screen hidden) credits up to now and stops the clock.
    now.wait(20)
    paused = beat(client, cid, "pause")["clock"]
    assert paused["running"] is False and paused["pending_seconds"] == 20


def test_rounds_mode_and_time_settings(timed, now):
    client, start = timed
    cid = start()
    v = view(client, cid)["campaign"]["version"]
    url = f"/api/v1/campaigns/{cid}/time-settings"
    assert client.put(url, json={"expected_version": v + 5, "light_timing": "rounds"}).status_code == 409
    assert client.put(url, json={"expected_version": v, "light_timing": "sundial"}).status_code == 422
    assert client.put(url, json={"expected_version": v, "idle_pause_seconds": 5}).status_code == 422
    state = client.put(url, json={"expected_version": v, "light_timing": "rounds"}).json()
    assert state["clock"]["timing"] == "rounds" and state["campaign"]["version"] == v + 1
    assert state["recent_events"][0]["type"] == "TIME_SETTINGS_CHANGED"

    lit = act(client, cid, "I light a torch")
    assert lit["state"]["clock"]["lights"][0]["remaining_minutes"] == 54  # one round of 6 minutes
    moved = act(client, cid, "I walk north to the drowned chapel")
    assert moved["state"]["clock"]["lights"][0]["remaining_minutes"] == 48
    # No real-time credit in rounds mode.
    beat(client, cid)
    now.wait(60)
    assert beat(client, cid)["clock"]["pending_seconds"] == 0


def test_ride_along_and_restart(timed, now):
    client, start = timed
    cid = start()
    act(client, cid, "I light a torch")
    beat(client, cid)
    now.wait(150)
    second = act(client, cid, "I light a torch")  # ride along: joins the current timer
    lights = second["state"]["clock"]["lights"]
    assert len(lights) == 2 and {li["remaining_minutes"] for li in lights} == {58}
    assert "shares the current light's timer" in " ".join(second["outcome"])

    cid2 = start()
    v = view(client, cid2)["campaign"]["version"]
    client.put(f"/api/v1/campaigns/{cid2}/time-settings", json={"expected_version": v, "light_multiple": "restart"})
    act(client, cid2, "I light a torch")
    beat(client, cid2)
    now.wait(150)
    second = act(client, cid2, "I light a torch")
    assert [li["remaining_minutes"] for li in second["state"]["clock"]["lights"]] == [60]
    assert any(e["type"] == "LIGHT_EXTINGUISHED" for e in second["events"])


def test_fuelled_light_needs_fuel(timed, now):
    client, start = timed
    cid = start(items=[{"item_key": "lamp", "quantity": 1}])
    out = act(client, cid, "I light the lamp")
    assert out["status"] == "REJECTED" and out["error"]["code"] == "NO_FUEL" and "Lamp oil" in out["error"]["message"]

    cid = start(items=[{"item_key": "lamp", "quantity": 1}, {"item_key": "lamp_oil", "quantity": 2}])
    out = act(client, cid, "I light the lamp")
    inv = {i["item_key"]: i["quantity"] for i in out["state"]["inventory"]}
    assert out["status"] == "COMMITTED" and inv == {"lamp": 1, "lamp_oil": 1}
    assert out["state"]["clock"]["lights"][0]["remaining_minutes"] == 45


def test_total_darkness_gives_disadvantage_on_sight_checks(timed, now):
    client, start = timed
    cid = start()
    dark = act(client, cid, "I spot for movement in the shadows")
    assert dark["state"]["clock"]["is_lit"] is False
    roll = dark["dice"][0]
    assert [d["kept"] for d in roll["dice"]].count(True) == 1 and len(roll["dice"]) == 2  # 15 and 3: keeps 3
    assert "total darkness" in " ".join(dark["outcome"])
    assert any(ref.endswith(":vision.total_darkness") for ref in roll["rule_refs"])

    act(client, cid, "I light a torch")
    lit = act(client, cid, "I spot for movement in the shadows")
    assert len(lit["dice"][0]["dice"]) == 1 and "total darkness" not in " ".join(lit["outcome"])
