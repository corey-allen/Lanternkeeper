# ADR-0001: Modular monolith with a separate ingestion worker

**Status:** Accepted (2026-10-29)

**Context.** One user, self-hosted. The design doc recommends avoiding microservices. Ingestion is
slow and failure-prone and must not block the API.

**Decision.** One API service plus a worker process built from the same image. Code is split into
three Python packages in a uv workspace: `lk_rules` (pure rules), `lk_dm` (provider/prompt/guards,
no DB), `lk_api` (HTTP, persistence, orchestration, ingestion, worker). Next.js web app separately.

**Consequences.** Simple deployment and transactions; clear dependency direction
(`lk_api` → `lk_dm`, `lk_rules`; never the reverse), enforced for `lk_rules` by a test.
