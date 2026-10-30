"""Character creation: one-shot for manual-entry profiles, a server-rolled draft for everything else.

Rolls happen only on the server and are recorded. A draft cannot be discarded and restarted, so
stats, hit points and talents cannot be rerolled except where the rules profile allows it.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from lk_rules.character import CharacterSpec, validate_character
from lk_rules.creation import (
    can_reroll_stats,
    choose_origin,
    finalize,
    reroll_stats,
    roll_class,
    set_manual_scores,
    start_draft,
)
from lk_rules import gear
from lk_rules.definitions import ClassDef
from lk_rules.errors import RulesError
from lk_rules.gear import Purchase
from lk_rules.inventory import Inventory, remove_item

from ..errors import ApiError
from ..game import creation_service as cs
from ..game import state as st
from ..game import tools
from ..models import Campaign, CampaignStatus, User
from ..security import CurrentUser

router = APIRouter()


def _setup_campaign(s, user: User, campaign_id: uuid.UUID) -> Campaign:
    c = st.load_campaign(s, user, campaign_id, lock=True)
    if c.status != CampaignStatus.SETUP:
        raise ApiError(409, "CAMPAIGN_STARTED", "Characters are created before the campaign starts")
    if st.character_of(s, c):
        raise ApiError(409, "CHARACTER_EXISTS", "This solo campaign already has a character")
    return c


def _require_draft(s, c: Campaign):
    row, draft = cs.load_draft(s, c, lock=True)
    if draft is None:
        raise ApiError(409, "NO_DRAFT", "Start character creation first")
    return row, draft


def _draft_view(s, c: Campaign, draft) -> dict[str, Any]:
    profile = st.profile_for(c)
    return {"draft": draft.model_dump(mode="json"), "can_reroll_stats": can_reroll_stats(draft, profile),
            "options": cs.draft_options(profile), "preview": cs.preview(draft, profile)}


@router.post("/campaigns/{campaign_id}/character", status_code=201)
def create_character(campaign_id: uuid.UUID, spec: CharacterSpec, request: Request, user: User = CurrentUser) -> dict:
    """One-shot creation for profiles whose scores are entered and nothing is rolled."""
    with request.app.state.db.session() as s:
        c = _setup_campaign(s, user, campaign_id)
        profile = st.profile_for(c)
        sheet = validate_character(spec, profile)  # RulesError -> 422 with every issue
        cs.persist_build(s, c, sheet.build, sheet.inventory, profile, request.app.state.services.roller)
        return st.player_view(s, c)


@router.get("/campaigns/{campaign_id}/character/draft")
def get_draft(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id)
        _, draft = cs.load_draft(s, c)
        if draft is None:
            return {"draft": None, "options": cs.draft_options(st.profile_for(c))}
        return _draft_view(s, c, draft)


@router.post("/campaigns/{campaign_id}/character/draft", status_code=201)
def begin_draft(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = _setup_campaign(s, user, campaign_id)
        if cs.load_draft(s, c)[1] is not None:
            raise ApiError(409, "DRAFT_EXISTS", "Character creation is already in progress")
        draft, rolls = start_draft(st.profile_for(c), request.app.state.services.roller)
        cs.record_rolls(s, c, rolls)
        cs.save_draft(s, c, draft)
        return _draft_view(s, c, draft)


@router.post("/campaigns/{campaign_id}/character/draft/reroll-stats")
def reroll(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = _setup_campaign(s, user, campaign_id)
        row, draft = _require_draft(s, c)
        draft, rolls = reroll_stats(draft, st.profile_for(c), request.app.state.services.roller)
        cs.record_rolls(s, c, rolls)
        cs.save_draft(s, c, draft, row)
        return _draft_view(s, c, draft)


class ScoresBody(BaseModel):
    scores: dict[str, int]


@router.put("/campaigns/{campaign_id}/character/draft/scores")
def set_scores(campaign_id: uuid.UUID, body: ScoresBody, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = _setup_campaign(s, user, campaign_id)
        row, draft = _require_draft(s, c)
        draft = set_manual_scores(draft, body.scores, st.profile_for(c))
        cs.save_draft(s, c, draft, row)
        return _draft_view(s, c, draft)


class OriginBody(BaseModel):
    ancestry_key: str | None = Field(default=None, max_length=64)
    class_key: str = Field(min_length=1, max_length=64)


@router.put("/campaigns/{campaign_id}/character/draft/origin")
def set_origin(campaign_id: uuid.UUID, body: OriginBody, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = _setup_campaign(s, user, campaign_id)
        row, draft = _require_draft(s, c)
        draft = choose_origin(draft, st.profile_for(c), ancestry_key=body.ancestry_key, class_key=body.class_key)
        cs.save_draft(s, c, draft, row)
        return _draft_view(s, c, draft)


@router.post("/campaigns/{campaign_id}/character/draft/roll")
def roll(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    """Roll hit points, level-1 talents and starting gold. Ancestry and class are locked afterwards."""
    with request.app.state.db.session() as s:
        c = _setup_campaign(s, user, campaign_id)
        row, draft = _require_draft(s, c)
        draft, rolls = roll_class(draft, st.profile_for(c), request.app.state.services.roller)
        cs.record_rolls(s, c, rolls)
        cs.save_draft(s, c, draft, row)
        return _draft_view(s, c, draft)


class DetailsBody(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    alignment: str | None = Field(default=None, max_length=32)
    background: str | None = Field(default=None, max_length=64)
    deity: str | None = Field(default=None, max_length=64)
    languages: list[str] | None = Field(default=None, max_length=10)
    trait_choices: dict[str, dict[str, Any]] | None = None
    talent_choices: dict[int, dict[str, Any]] | None = None  # index into draft.talents
    purchases: list[Purchase] | None = Field(default=None, max_length=40)  # starting gear


@router.put("/campaigns/{campaign_id}/character/draft/details")
def set_details(campaign_id: uuid.UUID, body: DetailsBody, request: Request, user: User = CurrentUser) -> dict:
    """Choices are stored as given and validated together at finalize (and in the preview)."""
    with request.app.state.db.session() as s:
        c = _setup_campaign(s, user, campaign_id)
        row, draft = _require_draft(s, c)
        update = body.model_dump(exclude_none=True, exclude={"talent_choices"})
        draft = type(draft).model_validate({**draft.model_dump(mode="json"), **update})  # validates choices
        if body.talent_choices:
            talents = [t.model_copy(deep=True) for t in draft.talents]
            for index, choices in body.talent_choices.items():
                if not 0 <= index < len(talents):
                    raise ApiError(422, "REQUEST_INVALID", f"No talent roll #{index}")
                talents[index] = talents[index].model_copy(update={"choices": choices})
            draft = draft.model_copy(update={"talents": talents})
        cs.save_draft(s, c, draft, row)
        return _draft_view(s, c, draft)


@router.post("/campaigns/{campaign_id}/character/draft/finalize", status_code=201)
def finalize_draft(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict:
    with request.app.state.db.session() as s:
        c = _setup_campaign(s, user, campaign_id)
        row, draft = _require_draft(s, c)
        profile = st.profile_for(c)
        build = finalize(draft, profile)  # RulesError -> 422 with every issue
        cs.persist_build(s, c, build, Inventory(), profile, request.app.state.services.roller)
        s.delete(row)
        s.flush()
        return st.player_view(s, c)


# ---- inventory management (direct, versioned actions; no model involved) ---------------------


class InventoryAction(BaseModel):
    item_key: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=0)
    hands: gear.Hands = "main_hand"
    quantity: int = Field(default=1, ge=1, le=999)


def _inventory_change(request: Request, user: User, campaign_id: uuid.UUID, body: InventoryAction, kind: str) -> dict:
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id, lock=True)
        if c.version != body.expected_version:
            raise ApiError(409, "VERSION_CONFLICT", "Campaign state changed; reload and retry",
                           current_version=c.version)
        ch = st.require_character(s, c)
        profile = st.profile_for(c)
        inv = st.inventory_model(st.inventory_rows(s, ch))
        stack = inv.get(body.item_key)
        if stack is None:
            raise ApiError(422, "ITEM_NOT_IN_INVENTORY", "You are not carrying that item")
        classes, _ = profile.get("character.classes", list[ClassDef])
        klass = next((k for k in classes if k.key == ch.class_key), None)
        if kind == "equip":
            inv = gear.equip(inv, body.item_key, profile, klass, hands=body.hands)
            summary = f"Equipped {stack.name}"
        elif kind == "unequip":
            inv = gear.unequip(inv, body.item_key)
            summary = f"Unequipped {stack.name}"
        else:
            inv = remove_item(inv, body.item_key, body.quantity)
            summary = f"Dropped {body.quantity} x {stack.name}"
        st.save_inventory(s, ch, inv)
        ctx = tools.ToolContext(session=s, campaign=c, character=ch, profile=profile,
                                roller=request.app.state.services.roller, action_id=None)
        before = ch.armor_class
        try:
            tools.refresh_armor_class(ctx)
        except RulesError:
            pass  # profile without armor rules: AC stays as recorded
        ctx.event(f"ITEM_{kind.upper()}", summary, {"item_key": body.item_key, "quantity": body.quantity,
                                                    "hands": body.hands, "ac_before": before, "ac_after": ch.armor_class})
        c.version += 1
        s.flush()
        return st.player_view(s, c)


@router.post("/campaigns/{campaign_id}/inventory/equip")
def equip_item(campaign_id: uuid.UUID, body: InventoryAction, request: Request, user: User = CurrentUser) -> dict:
    return _inventory_change(request, user, campaign_id, body, "equip")


@router.post("/campaigns/{campaign_id}/inventory/unequip")
def unequip_item(campaign_id: uuid.UUID, body: InventoryAction, request: Request, user: User = CurrentUser) -> dict:
    return _inventory_change(request, user, campaign_id, body, "unequip")


@router.post("/campaigns/{campaign_id}/inventory/drop")
def drop_item(campaign_id: uuid.UUID, body: InventoryAction, request: Request, user: User = CurrentUser) -> dict:
    return _inventory_change(request, user, campaign_id, body, "drop")
