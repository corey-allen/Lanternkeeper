"""FastAPI application factory: ``uvicorn lk_api.main:app``."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from sqlalchemy import select

from lk_dm.providers import ModelProvider, RetryPolicy, build_provider
from lk_rules import BUILTIN_PROFILES
from lk_rules.coverage import coverage
from lk_rules.dice import Roller, SecureRoller, SeededRoller

from .config import Settings, get_settings
from .db import Database
from .errors import install_error_handlers
from .game.orchestrator import Services
from .logging_setup import configure_logging
from .models import RuleReference, RulesCoverageRecord, RulesProfileRow, User
from .routes import campaigns, library, system
from .security import LoginRateLimiter, hash_password
from .storage import FileStorage

log = logging.getLogger("lk_api")


def sync_rules_profiles(db: Database) -> None:
    """Mirror built-in rules profiles into the database for auditing and coverage queries."""
    with db.session() as s:
        for p in BUILTIN_PROFILES.values():
            row = s.get(RulesProfileRow, p.key) or RulesProfileRow(id=p.key)
            row.name, row.version, row.system = p.name, p.version, p.system
            row.requires_house_rule_acceptance = p.requires_house_rule_acceptance
            row.definition = p.model_dump(mode="json")
            s.merge(row)
            s.flush()
            for key, entry in p.entries.items():
                ref = s.scalar(select(RuleReference).where(RuleReference.profile_id == p.key,
                                                           RuleReference.rule_key == key))
                ref = ref or RuleReference(profile_id=p.key, rule_key=key)
                ref.status, ref.source_citation, ref.notes = entry.status.value, entry.source, entry.notes
                s.add(ref)
            for c in coverage(p):
                rec = s.scalar(select(RulesCoverageRecord).where(RulesCoverageRecord.profile_id == p.key,
                                                                 RulesCoverageRecord.mechanic_key == c.mechanic))
                rec = rec or RulesCoverageRecord(profile_id=p.key, mechanic_key=c.mechanic)
                rec.status, rec.blocking_rules, rec.notes = c.status.value, list(c.blocking_rules), c.description
                s.add(rec)


def bootstrap_admin(db: Database, settings: Settings) -> None:
    with db.session() as s:
        if s.scalar(select(User.id).limit(1)):
            return
        if settings.admin_password is None or not settings.admin_password.get_secret_value():
            log.warning("no users exist and LK_ADMIN_PASSWORD is unset; sign-in is impossible until it is set")
            return
        pw = settings.admin_password.get_secret_value()
        if len(pw) < 12:
            raise RuntimeError("LK_ADMIN_PASSWORD must be at least 12 characters")
        s.add(User(username=settings.admin_username, password_hash=hash_password(pw)))
        log.info("bootstrap user created", extra={"username": settings.admin_username})


def create_app(settings: Settings | None = None, *, provider: ModelProvider | None = None,
               roller: Roller | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, include_document_text=settings.log_document_text)
    db = Database(settings.database_url)
    if provider is None:
        provider = build_provider(
            settings.ai_provider, base_url=settings.ai_base_url,
            api_key=settings.ai_api_key.get_secret_value() if settings.ai_api_key else None,
            allow_remote=settings.ai_allow_remote, local_hosts=settings.local_hosts)
    if roller is None:
        roller = SeededRoller(settings.dice_seed) if settings.dice_mode == "seeded" else SecureRoller()
    if roller.kind != "secure" and settings.environment != "test":
        raise RuntimeError("Non-secure dice rollers are only allowed in the test environment")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        sync_rules_profiles(db)
        bootstrap_admin(db, settings)
        log.info("api started", extra={"provider": provider.name, "provider_remote": provider.is_remote,
                                       "roller": roller.kind, "environment": settings.environment})
        yield
        db.dispose()

    app = FastAPI(title="Lanternkeeper API", version="0.1.0", docs_url="/api/docs",
                  openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.storage = FileStorage(settings.storage_dir)
    app.state.login_limiter = LoginRateLimiter()
    app.state.services = Services(db=db, provider=provider, roller=roller, model=settings.ai_model,
                                  timeout_seconds=settings.ai_timeout_seconds,
                                  retry=RetryPolicy(max_attempts=settings.ai_max_attempts))
    install_error_handlers(app)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "no-referrer"
        response.headers["cache-control"] = "no-store"
        return response

    for r in (system.router, library.router, campaigns.router):
        app.include_router(r, prefix="/api/v1")

    return app
