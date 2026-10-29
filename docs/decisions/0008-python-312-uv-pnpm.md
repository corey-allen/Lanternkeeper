# ADR-0008: Toolchain

**Status:** Accepted (2026-10-29)

**Decision.** Python 3.12 (uv-managed; `pgserver`'s embedded PostgreSQL has no 3.14 wheels and
the Docker base is `python:3.12-slim`). uv workspace with a lockfile. pnpm 9 for the web app.
Next.js 15.5.28 and React 19.1.10: the maintained security-backport lines at the time of writing,
chosen over older 15.1/19.0 releases affected by the 2025 React Server Components advisories.

**Consequences.** Plan an upgrade to the current Next.js major. Windows hosts without Developer
Mode must build the web app with `LK_WEB_STANDALONE=0` (standalone output needs symlinks).
