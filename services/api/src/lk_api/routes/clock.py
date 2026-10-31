"""The real-time play clock and light timing settings (ADR-0013).

The play screen sends heartbeats while it is visible and the player is active. The server measures
the gaps itself; the client never reports how much time passed.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from lk_rules.clock import timing_options

from ..errors import ApiError
from ..game import state as st
from ..game import tools
from ..models import CampaignStatus, User, utcnow
from ..security import CurrentUser

router = APIRouter()


def _tick(request: Request, user: User, campaign_id: uuid.UUID, *, pause: bool) -> dict[str, Any]:
    now = utcnow()
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id, lock=True)
        outcome: list[str] = []
        changed = False
        if c.status == CampaignStatus.ACTIVE:
            ctx = tools.ToolContext(session=s, campaign=c, character=st.require_character(s, c),
                                    profile=st.profile_for(c), roller=request.app.state.services.roller, action_id=None)
            tools.credit_real_time(ctx, now, pause=pause)
            tools.flush_real_time(ctx, only_at_deadline=True)
            if ctx.events:
                c.version += 1
                changed = True
            outcome = ctx.outcome
            s.flush()
        return {"campaign_version": c.version, "changed": changed, "outcome": outcome, "clock": st.clock_view(s, c)}


@router.post("/campaigns/{campaign_id}/clock/heartbeat")
def heartbeat(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict[str, Any]:
    """Credit active play time since the previous heartbeat (none if the gap exceeds the idle pause)."""
    return _tick(request, user, campaign_id, pause=False)


@router.post("/campaigns/{campaign_id}/clock/pause")
def pause(campaign_id: uuid.UUID, request: Request, user: User = CurrentUser) -> dict[str, Any]:
    """Credit time up to now and stop the clock (play screen hidden or closed)."""
    return _tick(request, user, campaign_id, pause=True)


class TimeSettings(BaseModel):
    expected_version: int = Field(ge=0)
    light_timing: str | None = Field(default=None, max_length=16)
    light_multiple: str | None = Field(default=None, max_length=16)
    idle_pause_seconds: int | None = Field(default=None, ge=30, le=3600)


@router.put("/campaigns/{campaign_id}/time-settings")
def set_time_settings(campaign_id: uuid.UUID, body: TimeSettings, request: Request,
                      user: User = CurrentUser) -> dict[str, Any]:
    with request.app.state.db.session() as s:
        c = st.load_campaign(s, user, campaign_id, lock=True)
        if c.version != body.expected_version:
            raise ApiError(409, "VERSION_CONFLICT", "Campaign state changed; reload and retry",
                           current_version=c.version)
        profile = st.profile_for(c)
        opts = timing_options(profile)
        if body.light_timing is not None and body.light_timing not in opts.modes:
            raise ApiError(422, "INVALID_TIME_SETTING", f"Timing mode must be one of {list(opts.modes)}")
        if body.light_multiple is not None and body.light_multiple not in opts.multiple_sources:
            raise ApiError(422, "INVALID_TIME_SETTING",
                           f"Multiple-light option must be one of {list(opts.multiple_sources)}")
        before = {"light_timing": st.timing_of(c, profile)[0], "light_multiple": st.timing_of(c, profile)[1],
                  "idle_pause_seconds": c.idle_pause_seconds}
        ctx = None
        if c.status == CampaignStatus.ACTIVE:
            ctx = tools.ToolContext(session=s, campaign=c, character=st.require_character(s, c), profile=profile,
                                    roller=request.app.state.services.roller, action_id=None)
            # Settle real time under the old settings before switching.
            tools.credit_real_time(ctx, utcnow(), pause=True)
            tools.flush_real_time(ctx, only_at_deadline=False)
        if body.light_timing is not None:
            c.light_timing = body.light_timing
        if body.light_multiple is not None:
            c.light_multiple = body.light_multiple
        if body.idle_pause_seconds is not None:
            c.idle_pause_seconds = body.idle_pause_seconds
        after = {"light_timing": st.timing_of(c, profile)[0], "light_multiple": st.timing_of(c, profile)[1],
                 "idle_pause_seconds": c.idle_pause_seconds}
        if ctx is not None:
            if after != before:
                ctx.event("TIME_SETTINGS_CHANGED", "Changed time settings",
                          {"before": before, "after": after, "rule_ref": timing_options(profile).rule_ref})
            if ctx.events:
                c.version += 1
        s.flush()
        return st.player_view(s, c)
