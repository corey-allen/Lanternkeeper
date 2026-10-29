# Testing strategy

Run everything: `uv run pytest` (116 tests: 115 pass, 1 skips without Tesseract, as of 2026-10-29).

Integration tests use **real PostgreSQL**: an embedded server from the `pgserver` dev dependency,
or `LK_TEST_DATABASE_URL` (e.g. CI service container). Each test creates a fresh database by
running the Alembic migrations, so the migrations are exercised on every run.

## Layers

| Layer | Location | Covers |
|-------|----------|--------|
| Rules unit | `packages/rules_engine/tests` | Dice parsing/rejection, roll records, rollers, advantage, profiles, unverified-profile blocking, unsupported mechanics, modifiers, character validation, checks, inventory, time/light, combat (initiative, hit/miss, damage, victory/defeat, turns), quests, conditions, the no-framework-imports boundary. |
| Orchestration unit | `packages/dm_orchestration/tests` | Fake provider interpretation, proposal schema has no outcome fields, data-section escaping, scripted invalid responses, timeout/retry policy, remote opt-in, OpenAI-compatible adapter (mock transport), injection detection, output guard. |
| API security | `services/api/tests/test_api_security.py` | Health, auth required everywhere, tampered tokens, rate limit, cookie flags, cross-user isolation, no download routes, configuration safety (seeded dice, insecure roller, remote AI, short key), house-rule acceptance, unverified profile, validation errors, coverage exposure. |
| Ingestion | `test_ingestion.py` | Upload validation and limits, private storage, page/passage provenance with bboxes, OCR fallback and OCR-unavailable reporting, duplicates/conflicts/broken refs/injection warnings, idempotent re-runs, transient retry, permanent failure, stale-lock recovery, review edit/approve/reject, approval-only authority, re-ingest refusal, deletion of originals and derived data. |
| Play loop | `test_play.py` | Full vertical slice incl. app restart and resume with identical state hash; idempotent retries; stale versions; light expiry; unsupported mechanics; invalid proposals; provider timeout; narration fallback; secret non-disclosure and output guard; prompt injection; NPC knowledge boundaries; deterministic combat; incapacitation without invented death rules; append-only events; export excludes source text; action audit. |
| Evaluation | `test_evals.py` + `fixtures/evals/saltmere_eval.json` | Metrics below. |
| Browser E2E | `apps/web/tests/vertical-slice.spec.ts` (Playwright) | Login → library → upload → review → campaign → character → play → reload. Needs a running stack and `LK_E2E_PASSWORD`. **Not yet run in CI**; the same flow was exercised manually in a browser on 2026-10-29. |

## Evaluation metrics

The harness plays each case in a fresh campaign, then restarts the app and re-checks state.

| Metric | Definition | Current (fake provider) |
|--------|------------|-------------------------|
| Unsupported fact rate | `VERIFIED_SOURCE` claims without a valid approved-passage citation / all such claims | 0.0 |
| Citation hit rate | Grounded cases whose expected passage (phrase + page) is cited | 1.0 |
| Secret disclosures | Restricted phrases in player-visible output or model input before discovery | 0 |
| NPC knowledge leaks | NPC memories containing facts that NPC could not have learned | 0 |
| State invariant violations | SQL invariants: HP bounds, positive quantities, gapless event sequence, version = max event version, positive lights, memory origin, entity provenance | 0 |
| Recovery correctness | Campaigns whose state hash is unchanged after restart | 1.0 |

**Interpretation:** with the deterministic fake provider these numbers measure the application's
guarantees (filtering, citations, guards, validation, persistence), not the prose quality or intent
accuracy of a real model. To evaluate a real model, point the same harness at a configured provider
(planned: `scripts/run_evals.py --provider openai_compatible`), and add human review of narration.
Plausible-sounding prose is never treated as success.

## Conventions

- Deterministic rollers (`SeededRoller`, `FixedRoller`, test-only cycling/scripted rollers) in tests
  only; the app refuses them outside `LK_ENVIRONMENT=test`.
- Fixtures are original synthetic content only.
- A failing test is reported as failing; skips state their reason (`tesseract` marker).
