"""DM orchestration: the action lifecycle.

1. Idempotency check and consistent snapshot (short read transaction).
2. Retrieve player-safe context (approved, visible, non-flagged passages only).
3. Ask the provider for a structured IntentProposal (no transaction open).
4. Validate the proposal against the scene's legal options and the rules profile.
5. Commit: lock campaign row, re-check version, run deterministic tools,
   append events and dice records, bump version (one transaction).
6. Narrate from the committed outcome (no transaction open), guard output.
7. Store the response on the ActionRecord.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from lk_dm.context import build_intent_messages, build_narration_messages
from lk_dm.providers import ModelProvider, ProviderError, RetryPolicy, call_with_policy
from lk_dm.safety import guard_output
from lk_dm.schemas import ClaimClass, ContextItem, IntentKind, IntentProposal, SceneOption, SceneOptions
from lk_rules import Ability, RulesError, RulesProfile, UnsupportedMechanic
from lk_rules.checks import task_tags
from lk_rules.coverage import require_supported
from lk_rules.dice import Roller

from ..db import Database
from ..errors import ApiError
from ..models import ActionRecord, ActionStatus, AdventureEntity, CampaignStatus, Location, NPCDefinition, User
from .. import retrieval
from . import state as st
from . import tools

log = logging.getLogger("lk_api.orchestrator")


class ActionRequest(BaseModel):
    input: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_\-:.]+$")
    expected_version: int = Field(ge=0)


@dataclass
class Services:
    db: Database
    provider: ModelProvider
    roller: Roller
    model: str
    timeout_seconds: float
    retry: RetryPolicy


def _hash(req: ActionRequest) -> str:
    return hashlib.sha256(f"{req.expected_version}\x00{req.input}".encode()).hexdigest()


@dataclass
class Snapshot:
    campaign_id: uuid.UUID
    adventure_id: uuid.UUID
    version: int
    options: SceneOptions
    items: list[ContextItem]
    allowed_passages: set[uuid.UUID]
    restricted: dict[str, str]
    profile: RulesProfile


def scene_items(s, campaign, allowed: set[uuid.UUID]) -> list[ContextItem]:
    """Player-safe context for the current scene: location, people, creatures, discoveries."""
    loc = s.get(AdventureEntity, campaign.current_location_id)
    loc_detail = s.get(Location, loc.id)

    def cites(entity_id):
        return tuple(h.ref() for h in retrieval.passages_for_entities(s, [entity_id], allowed))

    items = [ContextItem(kind="location", claim_class=ClaimClass.VERIFIED_SOURCE, title=loc.name,
                         text=loc_detail.description or loc.summary, entity_id=str(loc.id), citations=cites(loc.id))]
    for inst, e in st.npcs_present(s, campaign):
        d = s.get(NPCDefinition, e.id)
        items.append(ContextItem(kind="npc", claim_class=ClaimClass.VERIFIED_SOURCE, title=e.name,
                                 text=f"{d.description} Disposition: {inst.disposition.value}.",
                                 entity_id=str(e.id), citations=cites(e.id)))
    for e, m in st.enemies_present(s, campaign):
        items.append(ContextItem(kind="creature", claim_class=ClaimClass.VERIFIED_SOURCE, title=e.name,
                                 text=m.description, entity_id=str(e.id), citations=cites(e.id)))
    for d in st.player_view(s, campaign)["discoveries"]:
        items.append(ContextItem(kind="discovery", claim_class=ClaimClass.CAMPAIGN_FACT, title=d["name"],
                                 text=d["text"], entity_id=d["id"]))
    return items


def _snapshot(svc: Services, user: User, campaign_id: uuid.UUID, req: ActionRequest) -> Snapshot | dict:
    with svc.db.session() as s:
        campaign = st.load_campaign(s, user, campaign_id)
        existing = s.scalar(select(ActionRecord).where(ActionRecord.campaign_id == campaign.id,
                                                       ActionRecord.idempotency_key == req.idempotency_key))
        if existing and existing.status != ActionStatus.FAILED:
            if existing.request_hash != _hash(req):
                raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "This idempotency key was used for a different action")
            return {**(existing.response or {}), "replayed": True}
        if campaign.status != CampaignStatus.ACTIVE or campaign.current_location_id is None:
            raise ApiError(409, "CAMPAIGN_NOT_STARTED", "Start the campaign before playing")
        if campaign.version != req.expected_version:
            raise ApiError(409, "VERSION_CONFLICT", "Campaign state changed; reload and retry",
                           current_version=campaign.version)
        ch = st.require_character(s, campaign)
        profile = st.profile_for(campaign)
        discovered = st.discovered_ids(s, campaign.id)
        allowed = retrieval.allowed_passage_ids(s, campaign.adventure_id, discovered)
        loc = s.get(AdventureEntity, campaign.current_location_id)
        items = scene_items(s, campaign, allowed)
        npcs = st.npcs_present(s, campaign)
        enemies = st.enemies_present(s, campaign)
        difficulties: tuple[str, ...] = ()
        try:
            tags = tuple(task_tags(profile))
        except RulesError:
            tags = ()
        try:
            table, _ = profile.get("check.difficulty", dict[str, int])
            difficulties = tuple(table)
        except RulesError:
            pass
        options = SceneOptions(
            location=SceneOption(id=str(loc.id), name=loc.name),
            exits=tuple(SceneOption(id=x["location_id"], name=x["name"]) for x in st.exits(s, campaign, discovered)),
            npcs=tuple(SceneOption(id=str(e.id), name=e.name) for _, e in npcs),
            enemies=tuple(SceneOption(id=str(e.id), name=e.name) for e, _ in enemies),
            items=tuple(SceneOption(id=r.item_key, name=r.name) for r in st.inventory_rows(s, ch)),
            abilities=tuple(a.value for a in Ability), difficulties=difficulties, task_tags=tags,
            in_combat=st.active_encounter(s, campaign) is not None)
        return Snapshot(campaign.id, campaign.adventure_id, campaign.version, options, items, allowed,
                        st.restricted_texts(s, campaign, discovered), profile)


def _record_failure(svc: Services, campaign_id: uuid.UUID, req: ActionRequest, code: str, message: str,
                    proposal: dict | None = None) -> None:
    with svc.db.session() as s:
        rec = s.scalar(select(ActionRecord).where(ActionRecord.campaign_id == campaign_id,
                                                  ActionRecord.idempotency_key == req.idempotency_key))
        if rec is None:
            rec = ActionRecord(campaign_id=campaign_id, idempotency_key=req.idempotency_key,
                               request_hash=_hash(req), player_input=req.input, expected_version=req.expected_version,
                               status=ActionStatus.FAILED)
            s.add(rec)
        rec.status, rec.error_code = ActionStatus.FAILED, code
        rec.proposal = proposal
        rec.diagnostics = {"error": {"code": code, "message": message}}


def _validate(proposal: IntentProposal, snap: Snapshot) -> tuple[str, str] | None:
    """Return (code, message) if the proposal is not legal in this scene, else None."""
    o = snap.options
    ids = {
        IntentKind.MOVE: {x.id for x in o.exits},
        IntentKind.TALK: {x.id for x in o.npcs},
        IntentKind.ATTACK: {x.id for x in o.enemies},
    }
    if proposal.intent in ids and proposal.target_id not in ids[proposal.intent]:
        return "INVALID_TARGET", f"Proposed {proposal.intent.value} target is not available here"
    if proposal.intent == IntentKind.USE_ITEM and proposal.item_key not in {x.id for x in o.items}:
        return "ITEM_NOT_IN_INVENTORY", "You do not have that item."
    if proposal.intent == IntentKind.ABILITY_CHECK:
        if proposal.ability not in o.abilities:
            return "INVALID_ABILITY", "Unknown ability"
        if proposal.difficulty not in o.difficulties:
            return "INVALID_DIFFICULTY", "Difficulty must be one of the profile's labels"
        if proposal.task_tag is not None and proposal.task_tag not in o.task_tags:
            return "INVALID_TASK", "Task category must be one of the profile's categories"
    if o.in_combat and proposal.intent not in (IntentKind.ATTACK, IntentKind.ASK, IntentKind.UNSUPPORTED,
                                               IntentKind.CLARIFY):
        return "IN_COMBAT", "You are in combat. Only attacks are supported until it ends."
    return None


def _answer_items(svc: Services, snap: Snapshot, topic: str) -> list[ContextItem]:
    with svc.db.session() as s:
        hits = retrieval.search_passages(s, snap.adventure_id, topic, snap.allowed_passages)
    return [retrieval.as_context(h) for h in hits]


async def handle_action(svc: Services, user: User, campaign_id: uuid.UUID, req: ActionRequest) -> dict[str, Any]:
    snap = _snapshot(svc, user, campaign_id, req)
    if isinstance(snap, dict):
        return snap

    diagnostics: dict[str, Any] = {"context": [{"kind": i.kind, "title": i.title, "claim": i.claim_class.value,
                                                "citations": [c.passage_id for c in i.citations]} for i in snap.items]}
    # ---- 3. interpret (no transaction open) ---------------------------------
    messages = build_intent_messages(snap.options, snap.items, req.input)
    try:
        proposal = await call_with_policy(
            lambda: svc.provider.generate_structured(messages, IntentProposal, model=svc.model,
                                                     timeout_seconds=svc.timeout_seconds),
            timeout_seconds=svc.timeout_seconds, policy=svc.retry)
    except ProviderError as exc:
        _record_failure(svc, campaign_id, req, exc.code, str(exc))
        raise ApiError(503, exc.code, "The DM model could not interpret that action. Nothing was changed; "
                                      "you can retry with the same idempotency key.") from exc

    # ---- 4. validate --------------------------------------------------------
    problem = _validate(proposal, snap)
    answer_items: list[ContextItem] = []
    if proposal.intent == IntentKind.ASK and problem is None:
        answer_items = _answer_items(svc, snap, proposal.topic or req.input)

    # ---- 5. commit ----------------------------------------------------------
    ctx_out: dict[str, Any] = {}
    try:
        with svc.db.session() as s:
            campaign = st.load_campaign(s, user, campaign_id, lock=True)
            if campaign.version != req.expected_version:
                raise ApiError(409, "VERSION_CONFLICT", "Campaign state changed; reload and retry",
                               current_version=campaign.version)
            rec = s.scalar(select(ActionRecord).where(ActionRecord.campaign_id == campaign.id,
                                                      ActionRecord.idempotency_key == req.idempotency_key))
            if rec is None:
                rec = ActionRecord(campaign_id=campaign.id, idempotency_key=req.idempotency_key,
                                   request_hash=_hash(req), player_input=req.input,
                                   expected_version=req.expected_version, status=ActionStatus.FAILED)
                s.add(rec)
                s.flush()
            ctx = tools.ToolContext(session=s, campaign=campaign, character=st.require_character(s, campaign),
                                    profile=st.profile_for(campaign), roller=svc.roller, action_id=rec.id)
            status, error = _execute(ctx, proposal, problem)
            if ctx.events:
                campaign.version += 1
            rec.status = status
            rec.proposal = proposal.model_dump(mode="json")
            rec.validation = {"ok": error is None, **({"error": error} if error else {})}
            rec.result = {"outcome": ctx.outcome, "events": [e.sequence for e in ctx.events],
                          "rolls": [str(r.id) for r in ctx.rolls], "rule_refs": sorted(set(ctx.rule_refs))}
            rec.resulting_version = campaign.version
            rec.error_code = error["code"] if error else None
            rec.diagnostics = diagnostics
            s.flush()
            ctx_out = {"record_id": rec.id, "status": status, "error": error, "outcome": list(ctx.outcome),
                       "events": [st.event_dict(e) for e in ctx.events if e.visibility.value == "PLAYER_VISIBLE"],
                       "dice": [st.roll_dict(r) for r in ctx.rolls], "version": campaign.version,
                       "rule_refs": sorted(set(ctx.rule_refs)),
                       "restricted": st.restricted_texts(s, campaign, st.discovered_ids(s, campaign.id)),
                       # Narrate from the state AFTER the commit (e.g. the new room after a move).
                       "scene": scene_items(s, campaign, retrieval.allowed_passage_ids(
                           s, campaign.adventure_id, st.discovered_ids(s, campaign.id)))}
    except IntegrityError as exc:
        # A concurrent request with the same key committed first: return its result.
        log.info("idempotency race resolved", extra={"campaign_id": str(campaign_id)})
        again = _snapshot(svc, user, campaign_id, req)
        if isinstance(again, dict):
            return again
        raise ApiError(409, "VERSION_CONFLICT", "Concurrent update; reload and retry") from exc

    # ---- 6. narrate from committed outcome ----------------------------------
    claims = _claims(proposal, snap, answer_items, ctx_out)
    facts = ctx_out["scene"][:1] + answer_items + ctx_out["scene"][1:]
    outcome_lines = ctx_out["outcome"] or ([ctx_out["error"]["message"]] if ctx_out["error"] else [])
    narration_source = "model"
    try:
        text = await call_with_policy(
            lambda: svc.provider.generate_text(build_narration_messages(outcome_lines, facts, req.input),
                                               model=svc.model, timeout_seconds=svc.timeout_seconds),
            timeout_seconds=svc.timeout_seconds, policy=svc.retry)
    except ProviderError as exc:
        diagnostics["narration_error"] = exc.code
        text, narration_source = " ".join(outcome_lines), "fallback"
    guarded = guard_output(text, ctx_out["restricted"])
    if guarded.violations:
        diagnostics["guard_violations"] = list(guarded.violations)
        narration_source = "guarded"

    response = {
        "action_id": str(ctx_out["record_id"]), "status": ctx_out["status"].value, "replayed": False,
        "campaign_version": ctx_out["version"], "intent": proposal.intent.value,
        "narration": guarded.text, "narration_source": narration_source,
        "narration_note": "Narration is model-generated prose; outcomes, dice, and claims below are authoritative.",
        "outcome": ctx_out["outcome"], "claims": claims, "dice": ctx_out["dice"], "events": ctx_out["events"],
        "error": ctx_out["error"], "clarification": proposal.clarification if proposal.intent == IntentKind.CLARIFY else None,
    }
    with svc.db.session() as s:
        rec = s.get(ActionRecord, ctx_out["record_id"])
        rec.narration = guarded.text
        rec.response = response
        rec.diagnostics = diagnostics
    return response


def _execute(ctx: tools.ToolContext, p: IntentProposal, problem: tuple[str, str] | None):
    if problem:
        return ActionStatus.REJECTED, {"code": problem[0], "message": problem[1]}
    # Tools run inside a SAVEPOINT so a rejected tool leaves no partial state behind.
    savepoint = ctx.session.begin_nested()
    try:
        status, error = _dispatch(ctx, p)
    except UnsupportedMechanic as exc:
        status, error = ActionStatus.UNSUPPORTED, {"code": exc.code, "message": exc.message}
    except tools.ToolRejected as exc:
        status = ActionStatus.UNSUPPORTED if exc.code == "UNSUPPORTED_MECHANIC" else ActionStatus.REJECTED
        error = {"code": exc.code, "message": exc.message}
    except RulesError as exc:
        status, error = ActionStatus.REJECTED, {"code": exc.code, "message": exc.message}
    if error is None:
        savepoint.commit()
    else:
        savepoint.rollback()
        ctx.events.clear()
        ctx.rolls.clear()
        ctx.outcome.clear()
        ctx.rule_refs.clear()
    return status, error


def _dispatch(ctx: tools.ToolContext, p: IntentProposal):
    match p.intent:
        case IntentKind.ASK:
            return ActionStatus.ANSWERED, None
        case IntentKind.CLARIFY:
            return ActionStatus.CLARIFY, None
        case IntentKind.UNSUPPORTED:
            require_supported(p.unsupported_mechanic or "unknown")
            raise UnsupportedMechanic(p.unsupported_mechanic or "unknown",
                                      "It is implemented but not offered by this action path.")
        case IntentKind.MOVE:
            tools.move_to_location(ctx, p.target_id or "")
        case IntentKind.SEARCH:
            tools.search_location(ctx)
        case IntentKind.ABILITY_CHECK:
            tools.request_ability_check(ctx, p.ability or "", p.difficulty or "", "player-declared action",
                                        task_tag=p.task_tag)
            minutes, ref = ctx.profile.action_minutes("check")
            ctx.rule_refs.append(str(ref))
            tools.advance_game_time(ctx, minutes, "attempting an action")
        case IntentKind.TALK:
            tools.record_npc_interaction(ctx, p.target_id or "", p.topic or "")
        case IntentKind.ATTACK:
            tools.resolve_attack_tool(ctx, p.target_id or "")
        case IntentKind.USE_ITEM:
            tools.use_item(ctx, p.item_key or "")
        case IntentKind.WAIT:
            tools.wait(ctx, p.wait_minutes or 10)
    return ActionStatus.COMMITTED, None


def _claims(p: IntentProposal, snap: Snapshot, answer_items: list[ContextItem], out: dict) -> list[dict]:
    claims: list[dict] = []
    if p.intent == IntentKind.ASK:
        for item in snap.items[:1] + answer_items:
            claims.append({"claim_class": item.claim_class.value, "title": item.title, "text": item.text,
                           "citations": [c.model_dump() for c in item.citations]})
        for item in snap.items[1:]:
            claims.append({"claim_class": item.claim_class.value, "title": item.title, "text": item.text,
                           "citations": [c.model_dump() for c in item.citations]})
        if not answer_items and p.topic and len(p.topic.split()) > 2:
            claims.append({"claim_class": ClaimClass.UNKNOWN.value, "title": "Not found in approved sources",
                           "text": "No approved source passage answers this directly; anything beyond the cited "
                                   "facts would be improvisation.", "citations": []})
    for line in out["outcome"]:
        claims.append({"claim_class": ClaimClass.CAMPAIGN_FACT.value, "title": "Committed outcome", "text": line,
                       "citations": []})
    profile = snap.profile
    for ref in out["rule_refs"]:
        rule_key = ref.split(":", 1)[-1]
        entry = profile.entries.get(rule_key)
        status = entry.status.value if entry else "MISSING"
        claim = {"VERIFIED": ClaimClass.VERIFIED_RULE, "HOUSE_RULE": ClaimClass.HOUSE_RULE}.get(status, ClaimClass.UNKNOWN)
        claims.append({"claim_class": claim.value, "title": f"Rule applied ({status})", "text": ref,
                       "citations": [], "source": entry.source if entry else None})
    return claims
