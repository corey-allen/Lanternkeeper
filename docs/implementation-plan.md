# Implementation plan and status

Status as of 2026-10-29. "Verified here" means exercised on the development machine; see the notes.

## Baseline assessment (before this work)

The local folder contained only the owner's design document
(`Lanternkeeper_Shadowdark_AI_DM_Master_Design_Doc.docx`, kept out of git). The GitHub repository
`corey-allen/Lanternkeeper` existed and was empty. No code, configuration, tests, or secrets were
present, so the architecture in the design document was scaffolded fresh.

Development machine: Windows 11, Python 3.14 (project pinned to 3.12 via uv), git. No Docker,
Node.js, PostgreSQL, or Tesseract installed. PostgreSQL came from the `pgserver` package, and Node.js
from the `nodejs-wheel-binaries` package in a scratch environment.

## Phases

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Inspect repo; architecture, licensing review, rules coverage, threat model, ADRs | Done |
| 1 | Compose environment, migrations, health checks, campaign CRUD, frontend shell, tests | Done. Compose files written but **not run here** (no Docker); API, worker and web verified locally without Docker. |
| 2 | PDF extraction, OCR fallback, page provenance, synthetic fixture, candidates, review workflow | Done (heuristic extractor; Tesseract path untested here) |
| 3 | Rules subset, server dice and audit, character/state validation | Done for the dev house-rule profile; no Shadowdark rules (licensing gate) |
| 4 | Provider abstraction, context assembly, structured proposals, validated tools, explore/combat loop | Done (fake provider; OpenAI-compatible adapter unit-tested with mock transport only) |
| 5 | NPC memory, secrets, quests, events, recovery, evaluations | Done for the slice |
| 6 | Hardening, backup/restore test, deployment docs, acceptance | Partly: docs and scripts written; backup/restore and Compose not executed |

## Vertical slice: status

| Step | Status |
|------|--------|
| 1. Start with Docker Compose | **Not verified** (no Docker here). Equivalent local stack verified via `scripts/dev_stack.py` + `next start`. |
| 2–6. Campaign, upload, extract with page refs, ≥2 locations + NPC + clue + connection, review/approve | Verified by tests and in the browser (upload done through the web proxy with curl, because the browser tooling cannot set file inputs) |
| 7. Character via the verified subset | Uses the explicitly accepted dev house-rule profile, because no Shadowdark values are verified |
| 8–13. Play, grounded answer, supported action, deterministic resolution, commit, event | Verified (tests + browser) |
| 14–15. Save, restart, resume unchanged | Verified: test restarts the app instance; manually stopped and restarted Postgres, API and worker, then confirmed an identical state hash and resumed play in the browser |

## Known limitations

- Heuristic extraction only understands the documented keyed-entry convention.
- Tesseract OCR is wired but untested here. The OCR path is tested with a static adapter.
- Talking to NPCs records memories and reveals `on_talk` clues, but NPC dialogue is not yet a
  separate per-NPC model call with only that NPC's memories (the deterministic layer is in place).
- No retraction of approved candidates, entity merge tool, or evidence editing in the UI.
- No fleeing, critical hits, morale, dying, rest, advancement, spells, social mechanics.
- Per-document `allow_remote_ai` is recorded but not enforced per request.
- Single process rate limiter (in memory). No TLS, CSRF token, or user management.
- Playwright spec written but not run. The web Docker image was not built here.
- Synchronous DB work inside the async action endpoint (acceptable for one user).

## Next increment

Shadowdark rules from the owner's own rulebook, subsystem by subsystem, kept private:
[`plans/0002-shadowdark-rules-from-owner-copy.md`](plans/0002-shadowdark-rules-from-owner-copy.md).

## Other next steps

1. Run `docker compose up` on a Docker host and the Playwright spec against it; fix anything found.
2. Install Tesseract and run the `tesseract`-marked test; then enable `LK_OCR_ENGINE=tesseract` by default.
3. Owner decision on licensing questions Q1/Q3/Q5 (docs/licensing-review.md).
4. Per-NPC dialogue context (only that NPC's memories + definition) and enforce `allow_remote_ai`.
5. Candidate retraction/merge and evidence editing in the review UI.
6. CI workflow (GitHub Actions: Postgres service, `uv run pytest`, `pnpm build`, Playwright).
