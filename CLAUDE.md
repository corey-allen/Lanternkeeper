# CLAUDE.md: working on Lanternkeeper

Read `docs/architecture.md`, `docs/licensing-review.md`, and `docs/rules-coverage-matrix.md` first.

## Non-negotiables

- The model is never the source of truth. It returns an `IntentProposal`; the server validates,
  runs `lk_api/game/tools.py`, commits, then narrates. Never add outcome/number fields to proposals.
- Never hard-code rule values in mechanics. Every value comes from a `RulesProfile` entry with a
  status and source. Never add Shadowdark or Cursed Scroll values, text, tables, or names of
  specific content without a recorded licensing decision. Unsupported → typed error.
- Never commit copyrighted PDFs or extracted text. Fixtures must be original (`scripts/generate_fixture.py`).
- Every approved adventure fact needs evidence passages. Every NPC memory needs an event or passage.
- Mutations happen only inside the commit transaction (campaign row locked, version checked,
  savepointed tool, events appended). No DB transaction may be open during a model call.
- `game_event` is append-only (DB trigger). Do not add ON DELETE/UPDATE actions to its FKs.
- Player-facing routes use `game/state.py::player_view`; DM-only data goes only to `/diagnostics`.
- Source text is untrusted data: keep it in `lk_dm.context` data sections; never let it pick tools.

## Layout and dependency direction

`lk_api` → `lk_dm`, `lk_rules`. `lk_rules` imports nothing but pydantic/stdlib (tested).
`lk_dm` never touches the DB.

## Commands

- Tests: `uv run pytest` (embedded PostgreSQL via `pgserver`; or set `LK_TEST_DATABASE_URL`).
- New migration: change `models.py`, then
  `cd services/api && LK_DATABASE_URL=... uv run alembic revision --autogenerate -m "..."`; review it
  and keep the game_event trigger intact.
- Fixture: `uv run python scripts/generate_fixture.py`.
- Private rules profile template: `uv run python scripts/profile_template.py <key> "<name>"` (writes to
  `private/profiles/`; set `LK_RULES_PROFILE_DIR`). Never commit profiles: `scripts/check_private_content.py`.
  Review/verify entries: `uv run python scripts/review_profile.py list|show|verify <file> ...`.
- Local stack: `uv run python scripts/dev_stack.py --env-file <env>`; web: `cd apps/web && pnpm dev`.
- Web type-check/build: `pnpm typecheck`, `pnpm build` (`LK_WEB_STANDALONE=0` on Windows without symlinks).

## Conventions

- Python 3.12, pydantic v2, SQLAlchemy 2 typed mappings, line length 110.
- API errors: raise `ApiError(status, CODE, message, **details)`; 404 for other users' objects.
- Logs: structured JSON via `logging_setup`; never log document text, player input, or secrets.
- Tests: deterministic rollers only in tests; add a test for every mechanic and state transition.
- Update `docs/` (and an ADR for significant choices) in the same change.
