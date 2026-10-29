# Architecture

Lanternkeeper is a **modular monolith with a separate ingestion worker** (ADR-0001). Deterministic
code and the database own canonical state; the AI model only interprets, retrieves, and narrates.

```
 Browser ──HTTPS──▶ web (Next.js)  ── /api/* rewrite ──▶ api (FastAPI) ──▶ PostgreSQL
                                                          │   ▲               ▲
                                   lk_dm (providers,      │   │               │ SKIP LOCKED queue
                                   prompts, guards) ◀─────┘   │               │
                                   lk_rules (deterministic) ◀─┘          worker (same image)
                                                                             │
                                                          private file storage (volume)
```

## Components

| Component | Path | Responsibility | Must not |
|-----------|------|----------------|----------|
| Rules engine | `packages/rules_engine` (`lk_rules`) | Dice, profiles, character validation, checks, inventory, time/light, combat, quest transitions, coverage registry. Pure functions over typed pydantic models. | Import FastAPI, SQLAlchemy, HTTP clients, or providers (enforced by a test). |
| DM orchestration primitives | `packages/dm_orchestration` (`lk_dm`) | Provider protocol, fake and OpenAI-compatible adapters, retry/timeout policy, typed proposal schemas, prompt assembly with untrusted-data sections, injection flagging, output guard. | Touch the database. |
| API | `services/api` (`lk_api`) | Auth, ownership checks, upload validation, review/promotion, retrieval, campaign state, validated tools, the action lifecycle, DM diagnostics. | Expose source files; let the model mutate state directly. |
| Worker | `lk_api.worker` | Claims ingestion jobs and runs the idempotent pipeline. | Run model extraction by default (blocked pending licensing review). |
| Web | `apps/web` | Text-first UI: dashboard, library, ingestion status, review, graph workbench, character, play, DM console, rules coverage. | Talk to anything but its own origin. |
| PostgreSQL | — | All relational state, typed graph, events, full-text search, job queue. | Be published to the network. |

## API boundary

Versioned JSON API under `/api/v1`, documented at `/api/docs` (OpenAPI). Errors use one envelope:
`{"error": {"code", "message", "details"}}`. Every route except `/health` and `/auth/login`
requires a session (HttpOnly SameSite=Strict cookie, or `Authorization: Bearer` for scripts).
Every object lookup is filtered by `owner_id`; another user's object returns `404`.

Key routes: `auth/*`, `adventures`, `adventures/{id}/documents` (upload), `documents/{id}`
(status, warnings, pages, passages, reprocess, delete), `adventures/{id}/candidates`,
`candidates/{id}` (patch/approve/reject), `adventures/{id}/graph`, `campaigns` CRUD,
`campaigns/{id}/character`, `/start`, `/actions`, `/events`, `/snapshots`, `/state-hash`,
`/export`, `/diagnostics`, `rules/profiles`, `system/ai-disclosure`.

## Action lifecycle (`lk_api/game/orchestrator.py`)

1. **Idempotency and snapshot** (short read transaction): replay a stored response for a known
   key with the same request hash; `409 IDEMPOTENCY_KEY_REUSED` for a different request;
   `409 VERSION_CONFLICT` if `expected_version` is stale. Build player-safe context and the
   legal **scene options** (exit ids, NPC ids, enemy ids, inventory, abilities, difficulty labels).
2. **Interpret** (no transaction open): `provider.generate_structured(..., IntentProposal)` with a
   hard timeout and retry policy. `IntentProposal` has no fields for numbers or outcomes.
   Provider failure → `503`, a `FAILED` action record, no state change; the key may be retried.
3. **Validate**: targets must be in the scene options; difficulty must be a profile label; combat
   restricts intents; unsupported mechanics are checked against the coverage registry.
4. **Commit** (one transaction): lock the campaign row, re-check the version, run the mapped tool
   inside a SAVEPOINT (a rejected tool leaves nothing behind), append events and dice records,
   bump `campaign.version`, store the action record. The unique `(campaign_id, idempotency_key)`
   constraint resolves concurrent retries.
5. **Narrate** (no transaction open) from the committed outcome and the post-commit scene.
   Narration failure → deterministic fallback text. The output guard withholds narration that
   reproduces undiscovered DM-only text.
6. **Respond** with narration (labelled as model prose), outcome lines, typed claims with
   citations, dice records, events, and the fresh player view.

## AI provider abstraction

`ModelProvider` protocol: `generate_structured(messages, schema, model, timeout)` and
`generate_text(...)`. Adapters: `FakeProvider` (deterministic; scripted responses/exceptions for
tests) and `OpenAICompatibleProvider` (plain HTTP to any Chat Completions server: Ollama,
llama.cpp, LM Studio, vLLM, hosted services). No vendor SDK. `build_provider` refuses non-local
endpoints unless `LK_AI_ALLOW_REMOTE=true` (ADR-0005). Tool calling is not delegated to the
provider: the model returns one typed proposal and the application chooses the tool.

## Rules engine boundary

The API passes typed inputs (scores, inventory, combatants, clock) and receives typed results with
`RollRecord`s and rule references. Every value comes from the campaign's pinned `RulesProfile` version (ADR-0010): built-in profiles, or
the owner's private `*.profile.json` files loaded from `LK_RULES_PROFILE_DIR`. Missing
or unverified rules raise `UnverifiedRule`; unimplemented mechanics raise `UnsupportedMechanic`.
Production uses `SecureRoller` (`secrets.SystemRandom`); seeded rollers are refused outside
`LK_ENVIRONMENT=test`.

## Character creation

Server-rolled draft (`/campaigns/{id}/character/draft`: begin, reroll-stats, scores, origin, roll,
details, finalize) built on `lk_rules.creation`; every roll is recorded; the response includes the
profile's options and a live preview with all validation issues (ADR-0011).

## Checks

The model proposes an ability, a difficulty label and optionally a task category from the
profile's list (`SceneOptions.task_tags`). The server applies the character's stored modifiers, resolves
natural results from the profile, and grants automatic success only from a recorded trained-task feature
when not in combat. Each applied modifier is named in the outcome and stored on the event.

## Provenance and grounding

Source → `SourcePage` → `SourcePassage` (page number, bbox, method, injection flags) →
`ExtractionCandidate` + `CandidateEvidence` → human approval → `AdventureEntity` +
`EntityEvidence`. Retrieval filters by adventure, approval, visibility/discovery and injection flags
**before** ranking (`lk_api/retrieval.py`). Claims returned to the player are typed:
`VERIFIED_SOURCE` (with passage citations), `VERIFIED_RULE`, `HOUSE_RULE`, `CAMPAIGN_FACT`,
`INFERENCE`, `IMPROVISATION`, `UNKNOWN`.

## Campaign state and events

Canonical state lives in typed tables (character, inventory, conditions, location state,
discoveries, NPC instances and memories, quest state, encounter and participants, world clock,
active lights, defeated creatures). `game_event` is append-only, enforced by a PostgreSQL trigger;
every event carries the campaign version it produced. `campaign_snapshot` stores hash-verified
checkpoints that never replace the event log. Chat history is not state.

## Security and privacy controls

See `docs/threat-model.md`. Summary: single-user auth with scrypt hashes and HMAC session tokens,
login rate limiting, ownership checks, private storage with generated keys, upload validation,
structured logs that redact text, no source download route, remote-AI opt-in with UI disclosure,
deletion of originals and derived artifacts.

## Deployment, backup, restore

`infra/compose.yaml`: `db` (PostgreSQL 16, volume `pgdata`), `migrate` (Alembic, one-shot), `api`,
`worker`, `web` (published on `127.0.0.1` only). Health checks on db, api, and web.
`scripts/backup.sh` writes a `pg_dump -Fc` and a tarball of the storage volume with SHA-256 sums;
`scripts/restore.sh` restores both and re-runs migrations. Without Docker,
`scripts/dev_stack.py` runs the API and worker against an embedded PostgreSQL.

## Maps (deferred)

Connectivity has exactly one source of truth: `location_connection`. A future map layer will store
only presentation data (coordinates, image regions) keyed by location id and read connections from
that table (ADR-0006).
