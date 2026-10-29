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
from lk_rules.inventory import Inventory

from ..errors import ApiError
from ..game import creation_service as cs
from ..game import state as st
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


@router.put("/campaigns/{campaign_id}/character/draft/details")
def set_details(campaign_id: uuid.UUID, body: DetailsBody, request: Request, user: User = CurrentUser) -> dict:
    """Choices are stored as given and validated together at finalize (and in the preview)."""
    with request.app.state.db.session() as s:
        c = _setup_campaign(s, user, campaign_id)
        row, draft = _require_draft(s, c)
        update = body.model_dump(exclude_none=True, exclude={"talent_choices"})
        draft = draft.model_copy(update=update)
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
