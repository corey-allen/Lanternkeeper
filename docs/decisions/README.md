# Architecture decision records

| ADR | Decision | Status |
|-----|----------|--------|
| [0001](0001-modular-monolith.md) | Modular monolith + separate worker; three Python packages | Accepted |
| [0002](0002-postgres-only-graph-and-search.md) | PostgreSQL for relational graph and full-text search; no graph DB, no vector store yet | Accepted |
| [0003](0003-postgres-job-queue.md) | PostgreSQL `SKIP LOCKED` job queue instead of Redis | Accepted |
| [0004](0004-rules-profiles-and-house-rules.md) | Rule values live in profiles with status; ship no Shadowdark values; dev house-rule profile | Accepted, pending licensing review |
| [0005](0005-provider-neutral-ai-local-default.md) | Provider-neutral AI; fake provider default; remote opt-in | Accepted |
| [0006](0006-connectivity-single-source.md) | Location connectivity only in `location_connection` | Accepted |
| [0007](0007-proposal-validate-commit.md) | Model proposes a typed intent; the server validates, runs tools, commits, then narrates | Accepted |
| [0008](0008-python-312-uv-pnpm.md) | Python 3.12 via uv; pnpm; pinned patched Next/React | Accepted |

Template: Context, Decision, Consequences, Status. Supersede rather than edit accepted ADRs.
