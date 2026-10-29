"""Evaluation harness over the synthetic dataset.

With the deterministic fake provider this measures the APPLICATION's guarantees
(retrieval filtering, citations, guards, validation, invariants, recovery). It
does not measure the quality of a real model; run the same cases against a
configured provider for that (see docs/testing-strategy.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

from conftest import ROOT, login
from lk_dm.providers import FakeProvider
from lk_test_helpers import CyclingRoller, act, approve_all_sound, ingest, start_campaign

DATASET = json.loads((ROOT / "fixtures" / "evals" / "saltmere_eval.json").read_text(encoding="utf-8"))

INVARIANTS = {
    "hp_in_range": "SELECT count(*) FROM character WHERE hp_current < 0 OR hp_current > hp_max",
    "inventory_positive": "SELECT count(*) FROM character_inventory WHERE quantity <= 0",
    "event_sequence_gapless": """
        SELECT count(*) FROM (SELECT campaign_id, max(sequence) m, count(*) c FROM game_event GROUP BY campaign_id) t
        WHERE m <> c""",
    "version_matches_events": """
        SELECT count(*) FROM campaign c WHERE c.version <> coalesce(
            (SELECT max(campaign_version) FROM game_event e WHERE e.campaign_id = c.id), 0)""",
    "lights_positive": "SELECT count(*) FROM active_light WHERE remaining_minutes <= 0",
    "memories_have_origin": "SELECT count(*) FROM npc_memory WHERE event_id IS NULL AND passage_id IS NULL",
    "entities_have_provenance": """
        SELECT count(*) FROM adventure_entity e WHERE NOT EXISTS (
            SELECT 1 FROM entity_evidence v WHERE v.entity_id = e.id)""",
}


def _run_case(client, provider: FakeProvider, ids: dict, case: dict) -> dict:
    result = {"id": case["id"], "kind": case["kind"], "passed": True, "problems": []}
    cid = start_campaign(client, ids["adventure_id"], name=case["id"])["campaign_id"]
    result["campaign_id"] = cid
    for step in case["setup"]:
        act(client, cid, step)
    provider.calls.clear()
    out = act(client, cid, case["input"])
    passages = {p["id"]: p for p in client.get(f"/api/v1/documents/{ids['document_id']}/passages").json()}

    def fail(msg):
        result["passed"] = False
        result["problems"].append(msg)

    if out["status"] != case["expect_status"]:
        fail(f"status {out['status']} != {case['expect_status']}")
    source_claims = [c for c in out["claims"] if c["claim_class"] == "VERIFIED_SOURCE"]
    result["source_claims"] = len(source_claims)
    result["unsupported_source_claims"] = sum(
        1 for c in source_claims if not c["citations"] or any(x["passage_id"] not in passages for x in c["citations"]))
    if "expect_cited_phrase" in case:
        cited = [passages[x["passage_id"]] for c in source_claims for x in c["citations"]]
        hit = any(case["expect_cited_phrase"] in p["text"] and p["page_number"] == case["expect_citation_page"]
                  for p in cited)
        result["citation_hit"] = hit
        if not hit:
            fail("expected supporting passage not cited")
    if case.get("expect_unknown_claim") and not any(c["claim_class"] == "UNKNOWN" for c in out["claims"]):
        fail("missing UNKNOWN claim for an unsupported question")
    visible = json.dumps({k: out[k] for k in ("narration", "claims", "outcome", "events")}) + json.dumps(
        client.get(f"/api/v1/campaigns/{cid}").json())
    sent = "\n".join(m.content for call in provider.calls for m in call)
    discovered = {d["name"] for d in out["state"]["discoveries"]}
    leaks = [p for p in DATASET["restricted_phrases"]
             if (p in visible or p in sent) and "The Abbess's Bargain" not in discovered]
    result["secret_disclosures"] = len(leaks)
    if leaks:
        fail(f"restricted content exposed: {leaks}")
    if "npc_must_not_know" in case:
        diag = client.get(f"/api/v1/campaigns/{cid}/diagnostics").json()
        know = {n["npc"]: " ".join(m["content"] for m in n["memories"]) for n in diag["npc_knowledge"]}
        bad = case["npc_must_not_know"]
        good = case["npc_must_know"]
        result["npc_leaks"] = int(bad["phrase"] in know[bad["npc"]])
        if result["npc_leaks"]:
            fail(f"{bad['npc']} knows '{bad['phrase']}'")
        if good["phrase"] not in know[good["npc"]]:
            fail(f"{good['npc']} should know '{good['phrase']}'")
    if "expect_roll_purpose_prefix" in case and not any(
            d["purpose"].startswith(case["expect_roll_purpose_prefix"]) for d in out["dice"]):
        fail("expected a recorded roll")
    with client.app.state.db.engine.connect() as conn:
        violations = {k: conn.execute(sa.text(q)).scalar() for k, q in INVARIANTS.items()}
    result["invariant_violations"] = sum(violations.values())
    if result["invariant_violations"]:
        fail(f"invariants violated: {violations}")
    result["state_hash"] = client.get(f"/api/v1/campaigns/{cid}/state-hash").json()
    return result


def test_evaluation_dataset(app_factory, fixture_pdf, manifest, tmp_path: Path):
    provider = FakeProvider()
    with TestClient(app_factory(provider=provider, roller=CyclingRoller([15]))) as client:
        client.headers.update(login(client))
        adventure_id, document_id = ingest(client, fixture_pdf, manifest)
        approve_all_sound(client, adventure_id)
        ids = {"adventure_id": adventure_id, "document_id": document_id}
        results = [_run_case(client, provider, ids, case) for case in DATASET["cases"]]
    # Restart: a new app instance must see exactly the committed state of every campaign.
    with TestClient(app_factory(provider=FakeProvider())) as client2:
        client2.headers.update(login(client2))
        for r in results:
            r["recovered"] = client2.get(f"/api/v1/campaigns/{r['campaign_id']}/state-hash").json() == r["state_hash"]
            if not r["recovered"]:
                r["passed"] = False
                r["problems"].append("state changed across restart")
    source_claims = sum(r["source_claims"] for r in results)
    grounded = [r for r in results if "citation_hit" in r]
    report = {
        "dataset": DATASET["dataset"],
        "provider": "fake (deterministic)",
        "cases": len(results),
        "passed": sum(r["passed"] for r in results),
        "unsupported_fact_rate": (sum(r["unsupported_source_claims"] for r in results) / source_claims) if source_claims else 0.0,
        "citation_hit_rate": sum(r["citation_hit"] for r in grounded) / len(grounded),
        "secret_disclosures": sum(r["secret_disclosures"] for r in results),
        "npc_knowledge_leaks": sum(r.get("npc_leaks", 0) for r in results),
        "state_invariant_violations": sum(r["invariant_violations"] for r in results),
        "recovery_correctness": sum(r["recovered"] for r in results) / len(results),
        "failures": [r for r in results if not r["passed"]],
    }
    (tmp_path / "eval_report.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "failures"}, indent=2))
    assert report["failures"] == [], report["failures"]
    assert report["unsupported_fact_rate"] == 0.0
    assert report["citation_hit_rate"] == 1.0
    assert report["secret_disclosures"] == 0 and report["npc_knowledge_leaks"] == 0
    assert report["state_invariant_violations"] == 0 and report["recovery_correctness"] == 1.0
