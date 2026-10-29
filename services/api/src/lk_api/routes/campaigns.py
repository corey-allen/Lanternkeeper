"""Campaign CRUD, character creation, start, play actions, snapshots, export, and DM diagnostics."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from lk_rules import RulesProfile, RuleStatus
from lk_rules.character import CharacterSpec, validate_character
from lk_rules.coverage import coverage

from ..errors import ApiError, not_found
from ..game import state as st
from ..game import tools
from ..game.orchestrator import ActionRequest, handle_action
from ..models import (
    ActionRecord,
    Adventure,
    AdventureEntity,
    Campaign,
    CampaignSnapshot,
    CampaignStatus,
    Character,
    CharacterInventory,
    EntityType,
    GameEvent,
    HouseRule,
    NPCInstance,
    RulesProfileRow,
    RulesProfileVersion,
    User,
    Visibility,
)
from ..security import CurrentUser

router = APIRouter()


def _summary(s, c: Campaign) -> dict[str, Any]:
    ch = st.character_of(s, c)
    loc = s.get(AdventureEntity, c.current_location_id) if c.current_location_id else None
    last = s.scalar(select(GameEvent).where(GameEvent.campaign_id == c.id,
                                            GameEvent.visibility == Visibility.PLAYER_VISIBLE)
                    .order_by(GameEvent.sequence.desc()).limit(1))
    return {"id": str(c.id), "name": c.name, "status": c.status.value, "version": c.version,
            "rules_profile": c.rules_profile_id, "adventure_id": str(c.adventure_id) if c.adventure_id else None,
            "character": {"name": ch.name, "hp_current": ch.hp_current, "hp_max": ch.hp_max} if ch else None,
            "location": loc.name if loc else None, "last_event": last.summary if last else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rules_profile: str = Field(default="lk-dev-house-rules", max_length=64)
    accept_house_rules: bool = False
    adventure_id: uuid.UUID | None = None


def _current_version(s, profile: RulesProfile) -> RulesProfileVersion:
    version = s.scalar(select(RulesProfileVersion).where(RulesProfileVersion.profile_id == profile.key,
                                                         RulesProfileVersion.content_hash == profile.content_hash()))
    if version is None:  # synced at startup; absence means the registry changed without a restart
        raise ApiError(409, "RULES_PROFILE_NOT_SYNCED", "Restart the API to load this rules profile version")
    return version


def _record_house_rules(s, campaign: Campaign, profile: RulesProfile, user: User) -> None:
    for key, entry in profile.entries.items():
        if entry.status == RuleStatus.HOUSE_RULE:
            s.add(HouseRule(campaign_id=campaign.id, profile_id=profile.key, rule_key=key, accepted_by=user.id,
                            description=f"{entry.source}. {entry.notes}".strip()))


def _require_house_rule_acceptance(profile: RulesProfile, accepted: bool) -> None:
    if profile.needs_house_rule_acceptance and not accepted:
        raise ApiError(422, "HOUSE_RULES_NOT_ACCEPTED",
                       f"Profile '{profile.name}' contains house rules (not official rules). Review them under "
                       "Rules and set accept_house_rules=true to use it.")


@router.post("/campaigns", status_code=201)
def create_campaign(body: CampaignCreate, request: Request, user: User = CurrentUser) -> dict:
    try:
        profile = request.app.state.profiles.get(body.rules_profile)
    except KeyError as exc:
        raise ApiError(422, "UNKNOWN_RULES_PROFILE", f"Unknown rules profile {body.rules_profile!r}") from exc
    _require_house_rule_acceptance(profile, body.accept_house_rules)
    with request.app.state.db.session() as s:
        if body.adventure_id and not s.scalar(select(Adventure.id).where(Adventure.id == body.adventure_id,
                                                                          Adventure.owner_id == user.id)):
            raise not_found("Adventure")
        c = Campaign(owner_id=user.id, name=body.name.strip(), rules_profile_id=profile.key,
                     rules_profile_version_id=_current_version(s, profile).id,
                     adventure_id=body.adventure_id, status=CampaignStatus.SETUP, version=0,
                     house_rules_accepted_at=datetime.now(UTC) if body.accept_house_rules else None)
        s.add(c)
        s.flush()
        _record_house_rules(s, c, profile, user)
        return _summary(s, c)


@router.get("/campaigns/{campaign_id}/rules-profile")
def rules_profile_status(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    """Which profile version this campaign is pinned to, and whether a newer one is loaded."""
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id)
        pinned = s.get(RulesProfileVersion, c.rules_profile_version_id) if c.rules_profile_version_id else None
        row = s.get(RulesProfileRow, c.rules_profile_id)
        return {"profile": c.rules_profile_id,
                "pinned": None if pinned is None else {"version": pinned.version, "content_hash": pinned.content_hash},
                "available": None if row is None else {"version": row.version, "content_hash": row.content_hash},
                "update_available": bool(row and pinned and row.content_hash != pinned.content_hash)}


class ProfileUpgrade(BaseModel):
    accept_house_rules: bool = False


@router.post("/campaigns/{campaign_id}/rules-profile/upgrade")
def upgrade_rules_profile(campaign_id: uuid.UUID, body: ProfileUpgrade, request: Request,
                          user: User = CurrentUser) -> dict:
    """Explicitly move a campaign to the currently loaded version of its rules profile.

    Values already derived (e.g. max HP, AC at creation) are not recomputed; the event records both
    hashes so the change is auditable.
    """
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id, lock=True)
        try:
            profile = request.app.state.profiles.get(c.rules_profile_id)
        except KeyError as exc:
            raise ApiError(409, "RULES_PROFILE_UNAVAILABLE", "The campaign's rules profile is no longer loaded") from exc
        target = _current_version(s, profile)
        if target.id == c.rules_profile_version_id:
            raise ApiError(409, "RULES_PROFILE_CURRENT", "The campaign already uses the loaded version")
        if st.active_encounter(s, c):
            raise ApiError(409, "IN_COMBAT", "Finish the current combat before changing rules")
        _require_house_rule_acceptance(profile, body.accept_house_rules)
        previous = s.get(RulesProfileVersion, c.rules_profile_version_id) if c.rules_profile_version_id else None
        c.rules_profile_version_id = target.id
        _record_house_rules(s, c, profile, user)
        ctx = tools.ToolContext(session=s, campaign=c, character=st.character_of(s, c), profile=profile,
                                roller=request.app.state.services.roller, action_id=None)
        ctx.event("RULES_PROFILE_UPGRADED", f"Rules profile updated to {profile.key}@{profile.version}",
                  {"from_hash": previous.content_hash if previous else None, "to_hash": target.content_hash,
                   "note": "Previously derived values were not recomputed."})
        c.version += 1
        return {"profile": profile.key, "version": target.version, "content_hash": target.content_hash,
                "campaign_version": c.version}


@router.get("/campaigns")
def list_campaigns(request: Request, user: User = CurrentUser) -> list[dict]:
    with request.app.state.db.session() as s:
        return [_summary(s, c) for c in s.scalars(select(Campaign).where(Campaign.owner_id == user.id)
                                                  .order_by(Campaign.updated_at.desc()))]


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        return st.player_view(s, st.load_campaign(s, user, campaign_id))


class CampaignPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    adventure_id: uuid.UUID | None = None


@router.patch("/campaigns/{campaign_id}")
def patch_campaign(campaign_id: uuid.UUID, body: CampaignPatch, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id, lock=True)
        if body.name:
            c.name = body.name.strip()
        if body.adventure_id is not None:
            if c.status != CampaignStatus.SETUP:
                raise ApiError(409, "CAMPAIGN_STARTED", "The adventure cannot change after the campaign starts")
            if not s.scalar(select(Adventure.id).where(Adventure.id == body.adventure_id, Adventure.owner_id == user.id)):
                raise not_found("Adventure")
            c.adventure_id = body.adventure_id
        return _summary(s, c)


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id, lock=True)
        tools.clear_campaign_events_guard(s)
        s.delete(c)
    return {"deleted": True}


@router.post("/campaigns/{campaign_id}/character", status_code=201)
def create_character(campaign_id: uuid.UUID, spec: CharacterSpec, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id, lock=True)
        if c.status != CampaignStatus.SETUP:
            raise ApiError(409, "CAMPAIGN_STARTED", "Characters are created before the campaign starts")
        if st.character_of(s, c):
            raise ApiError(409, "CHARACTER_EXISTS", "This solo campaign already has a character")
        sheet = validate_character(spec, st.profile_for(c))  # RulesError -> 422 with every issue
        ch = Character(campaign_id=c.id, name=sheet.name, class_key=sheet.class_key, level=sheet.level,
                       str_score=spec.ability_scores.STR, dex_score=spec.ability_scores.DEX,
                       con_score=spec.ability_scores.CON, int_score=spec.ability_scores.INT,
                       wis_score=spec.ability_scores.WIS, cha_score=spec.ability_scores.CHA,
                       hp_current=sheet.current_hp, hp_max=sheet.max_hp, armor_class=sheet.armor_class,
                       rule_refs=list(sheet.rule_refs))
        s.add(ch)
        s.flush()
        for stack in sheet.inventory.stacks:
            s.add(CharacterInventory(character_id=ch.id, item_key=stack.item_key, name=stack.name,
                                     quantity=stack.quantity, slots_each=stack.slots_each))
        ctx = tools.ToolContext(session=s, campaign=c, character=ch, profile=st.profile_for(c),
                                roller=request.app.state.services.roller, action_id=None)
        ctx.event("CHARACTER_CREATED", f"{ch.name} the {sheet.class_name} joined the campaign",
                  {"name": ch.name, "class_key": ch.class_key, "max_hp": ch.hp_max, "rule_refs": list(sheet.rule_refs)})
        c.version += 1
        s.flush()
        return st.player_view(s, c)


class StartRequest(BaseModel):
    starting_location_id: uuid.UUID | None = None


@router.post("/campaigns/{campaign_id}/start")
def start_campaign(campaign_id: uuid.UUID, body: StartRequest, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id, lock=True)
        if c.status != CampaignStatus.SETUP:
            raise ApiError(409, "CAMPAIGN_STARTED", "Campaign already started")
        if c.adventure_id is None:
            raise ApiError(409, "NO_ADVENTURE", "Attach an adventure first")
        ch = st.require_character(s, c)
        locations = s.scalars(select(AdventureEntity).where(AdventureEntity.adventure_id == c.adventure_id,
                                                            AdventureEntity.entity_type == EntityType.LOCATION)
                              .order_by(AdventureEntity.source_order)).all()
        if not locations:
            raise ApiError(409, "NO_APPROVED_LOCATIONS", "Approve at least one location in the review workbench")
        start = locations[0]
        if body.starting_location_id:
            start = next((x for x in locations if x.id == body.starting_location_id), None)
            if start is None:
                raise not_found("Location")
        ctx = tools.ToolContext(session=s, campaign=c, character=ch, profile=st.profile_for(c),
                                roller=request.app.state.services.roller, action_id=None)
        tools.start_campaign(ctx, start)
        c.status = CampaignStatus.ACTIVE
        c.version += 1
        s.flush()
        return st.player_view(s, c)


@router.post("/campaigns/{campaign_id}/actions")
async def act(campaign_id: uuid.UUID, body: ActionRequest, request: Request, user: User = CurrentUser) -> dict:
    response = await handle_action(request.app.state.services, user, campaign_id, body)
    with request.app.state.db.session() as s:
        response["state"] = st.player_view(s, st.load_campaign(s, user, campaign_id))
    return response


@router.get("/campaigns/{campaign_id}/events")
def events(campaign_id: uuid.UUID, request: Request, limit: int = 50, user: User = CurrentUser) -> list[dict]:
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id)
        rows = s.scalars(select(GameEvent).where(GameEvent.campaign_id == c.id,
                                                 GameEvent.visibility == Visibility.PLAYER_VISIBLE)
                         .order_by(GameEvent.sequence.desc()).limit(min(max(limit, 1), 500)))
        return [st.event_dict(e) for e in rows]


@router.get("/campaigns/{campaign_id}/state-hash")
def state_hash(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id)
        state = st.canonical_state(s, c)
        return {"version": c.version, "state_hash": st.state_hash(state)}


class SnapshotCreate(BaseModel):
    label: str = Field(default="", max_length=120)


@router.post("/campaigns/{campaign_id}/snapshots", status_code=201)
def save(campaign_id: uuid.UUID, body: SnapshotCreate, request: Request, user: User = CurrentUser) -> dict:
    """'Save': every action is already committed; a snapshot adds a labelled, hash-verified checkpoint."""
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id)
        state = st.canonical_state(s, c)
        snap = CampaignSnapshot(campaign_id=c.id, campaign_version=c.version, label=body.label, state=state,
                                state_hash=st.state_hash(state))
        s.add(snap)
        s.flush()
        return {"id": str(snap.id), "campaign_version": snap.campaign_version, "state_hash": snap.state_hash,
                "label": snap.label, "created_at": snap.created_at.isoformat()}


@router.get("/campaigns/{campaign_id}/snapshots")
def list_snapshots(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> list[dict]:
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id)
        current = st.state_hash(st.canonical_state(s, c))
        return [{"id": str(x.id), "campaign_version": x.campaign_version, "state_hash": x.state_hash,
                 "label": x.label, "created_at": x.created_at.isoformat(), "matches_current": x.state_hash == current}
                for x in s.scalars(select(CampaignSnapshot).where(CampaignSnapshot.campaign_id == c.id)
                                   .order_by(CampaignSnapshot.created_at.desc()))]


@router.get("/campaigns/{campaign_id}/export")
def export(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    """Campaign-state export. Deliberately excludes source documents and extracted source text."""
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id)
        evs = s.scalars(select(GameEvent).where(GameEvent.campaign_id == c.id).order_by(GameEvent.sequence))
        return {"format": "lanternkeeper.campaign-export.v1",
                "note": "Contains campaign state and event history only. Source documents and extracted "
                        "adventure text are not included; entity ids refer to your private adventure library.",
                "state": st.canonical_state(s, c),
                "events": [{"sequence": e.sequence, "type": e.event_type, "summary": e.summary,
                            "minute": e.game_minute, "version": e.campaign_version} for e in evs]}


@router.get("/campaigns/{campaign_id}/diagnostics")
def diagnostics(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    """DM console. Contains spoilers (DM-only content); never used by player-facing views."""
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id)
        actions = s.scalars(select(ActionRecord).where(ActionRecord.campaign_id == c.id)
                            .order_by(ActionRecord.created_at.desc()).limit(25))
        evs = s.scalars(select(GameEvent).where(GameEvent.campaign_id == c.id)
                        .order_by(GameEvent.sequence.desc()).limit(50))
        npcs = s.execute(select(NPCInstance, AdventureEntity)
                         .join(AdventureEntity, AdventureEntity.id == NPCInstance.npc_entity_id)
                         .where(NPCInstance.campaign_id == c.id)).all()
        return {
            "spoiler_warning": "DM-only view: includes undiscovered secrets and hidden state.",
            "campaign_version": c.version,
            "actions": [{"id": str(a.id), "input": a.player_input, "status": a.status.value,
                         "proposal": a.proposal, "validation": a.validation, "result": a.result,
                         "diagnostics": a.diagnostics, "error_code": a.error_code,
                         "created_at": a.created_at.isoformat()} for a in actions],
            "events": [st.event_dict(e, include_payload=True) for e in evs],
            "npc_knowledge": [{"npc": e.name, "disposition": i.disposition.value,
                               "memories": tools.get_npc_knowledge(s, c, e.id)} for i, e in npcs],
            "restricted": st.restricted_texts(s, c, st.discovered_ids(s, c.id)),
            "canonical_state": st.canonical_state(s, c),
            "rules_coverage": [r.model_dump(mode="json") for r in coverage(st.profile_for(c))],
            "house_rules": [{"rule_key": h.rule_key, "description": h.description}
                            for h in s.scalars(select(HouseRule).where(HouseRule.campaign_id == c.id))],
        }


