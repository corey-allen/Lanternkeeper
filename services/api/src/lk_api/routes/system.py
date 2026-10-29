"""Health, authentication, AI disclosure, and rules profile routes."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from lk_rules.coverage import coverage

from ..errors import ApiError
from ..models import User
from ..security import COOKIE_NAME, CurrentUser, issue_token, verify_password

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict:
    db_ok = True
    try:
        with request.app.state.db.session() as s:
            s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - health reports, never raises
        db_ok = False
    status = "ok" if db_ok else "degraded"
    return {"status": status, "database": "ok" if db_ok else "unavailable", "version": "0.1.0"}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    settings = request.app.state.settings
    limiter = request.app.state.login_limiter
    key = f"{request.client.host if request.client else 'unknown'}:{body.username.lower()}"
    if limiter.blocked(key):
        raise ApiError(429, "TOO_MANY_ATTEMPTS", "Too many failed sign-in attempts; wait and retry")
    with request.app.state.db.session() as s:
        user = s.scalar(select(User).where(User.username == body.username, User.is_active.is_(True)))
        ok = user is not None and verify_password(body.password, user.password_hash)
        if not ok:
            limiter.fail(key)
            raise ApiError(401, "INVALID_CREDENTIALS", "Invalid username or password")
        token = issue_token(user.id, settings.secret_key.get_secret_value(), settings.session_hours)
        response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", secure=settings.cookie_secure,
                            max_age=settings.session_hours * 3600, path="/")
        return {"user": {"id": str(user.id), "username": user.username}, "token": token}


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = CurrentUser) -> dict:
    return {"id": str(user.id), "username": user.username}


@router.get("/system/ai-disclosure")
def ai_disclosure(request: Request, user: User = CurrentUser) -> dict:
    settings = request.app.state.settings
    provider = request.app.state.services.provider
    remote = bool(getattr(provider, "is_remote", False))
    return {
        "provider": provider.name, "model": settings.ai_model, "is_remote": remote,
        "remote_allowed": settings.ai_allow_remote,
        "message": ("Player input, campaign state, and approved adventure excerpts are sent to a REMOTE model "
                    "service when you play." if remote else
                    "No content leaves this server: the configured model runs locally or is the offline fake provider."),
    }


@router.get("/rules/profiles")
def rules_profiles(request: Request, user: User = CurrentUser) -> list[dict]:
    registry = request.app.state.profiles
    return [{
        "key": p.key, "name": p.name, "version": p.version, "system": p.system, "description": p.description,
        "requires_house_rule_acceptance": p.needs_house_rule_acceptance,
        "private": registry.is_private(p.key), "content_hash": p.content_hash(),
        "entries": {k: {"status": e.status.value, "source": e.source, "notes": e.notes,
                        "value": e.value if e.status.value != "UNVERIFIED" else None} for k, e in p.entries.items()},
        "coverage": [r.model_dump(mode="json") for r in coverage(p)],
    } for p in registry.profiles.values()]
