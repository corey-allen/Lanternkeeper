# Lanternkeeper

A self-hosted, solo-player, text-first AI Dungeon Master. You upload your own adventure PDFs,
review what the system extracts, and play. Deterministic code resolves the rules and owns campaign
state. The AI model only interprets what you say and narrates outcomes that have already been
committed.

> **Status: first vertical slice (v0.1).** It ships **no Shadowdark rules or content**. Play
> uses an original development house-rule profile that you accept explicitly. Read
> [`docs/licensing-review.md`](docs/licensing-review.md) before using it with published material.

## What works

- Private PDF upload with recorded rights acknowledgement, hashing and limits.
- Page-level text extraction with block coordinates, an OCR fallback adapter, passages with
  provenance, and prompt-injection flagging.
- Candidate entities with evidence; warnings for duplicates, conflicts, broken references and OCR.
- A review workflow (edit, approve, reject). Only approved items become adventure facts.
- An adventure graph (locations, connections, NPCs, monsters, clues, secrets, quests, typed edges)
  with a workbench view.
- Rules-validated character creation. Checks, inventory, time, light, and combat run on the
  deterministic rules engine with server-side, recorded dice.
- A natural-language play loop: answers grounded in approved passages with citations, typed
  claims, validated tools, idempotent and versioned commits, an append-only event log, NPC memory
  with knowledge boundaries, secret filtering with an output guard, a DM diagnostic console, save
  checkpoints, export, and resume after restart.

See [`docs/implementation-plan.md`](docs/implementation-plan.md) for exactly what is verified and what is not.

## Quick start (Docker Compose)

```bash
cp .env.example .env    # fill POSTGRES_PASSWORD, LK_SECRET_KEY, LK_ADMIN_PASSWORD
docker compose -f infra/compose.yaml --env-file .env up -d --build
```

Open http://127.0.0.1:3000 and sign in with `LK_ADMIN_USERNAME` / `LK_ADMIN_PASSWORD`.
Try it with the original synthetic adventure in `fixtures/synthetic_adventures/lantern_under_saltmere.pdf`.

Backup and restore: `sh scripts/backup.sh` and `sh scripts/restore.sh <db.dump> <storage.tgz>`.

## Development without Docker

```bash
uv sync                                                       # Python 3.12 workspace + dev tools
uv run pytest                                                 # all tests (embedded PostgreSQL)
uv run python scripts/dev_stack.py --env-file path/to/dev.env # API :8000 + worker, embedded PostgreSQL
cd apps/web && pnpm install && LK_API_INTERNAL_URL=http://127.0.0.1:8000 pnpm dev
```

`dev.env` needs `LK_SECRET_KEY` (32+ characters) and `LK_ADMIN_PASSWORD` (12+ characters).

## AI providers

`LK_AI_PROVIDER=fake` (the default) is an offline, deterministic interpreter and narrator.
`openai_compatible` talks to any Chat Completions server, for example Ollama at
`http://host.docker.internal:11434/v1`. Remote hosts are refused unless `LK_AI_ALLOW_REMOTE=true`.
The UI always shows whether content leaves the server.

## Repository layout

```
apps/web/                 Next.js UI
packages/rules_engine/    lk_rules - deterministic mechanics (no framework deps)
packages/dm_orchestration lk_dm    - providers, prompts, safety (no DB)
services/api/             lk_api   - FastAPI, models, migrations, ingestion, worker, play loop
infra/                    Compose + Dockerfiles
fixtures/                 original synthetic adventure + evaluation dataset
docs/                     architecture, data model, ingestion, threat model, licensing, rules, tests, ADRs
scripts/                  fixture generator, dev stack, backup/restore
```
