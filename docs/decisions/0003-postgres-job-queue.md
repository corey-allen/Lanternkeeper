# ADR-0003: PostgreSQL-backed job queue

**Status:** Accepted (2026-10-29)

**Context.** The brief allows Redis "if appropriate". Job volume is a handful of uploads.

**Decision.** `ingestion_job` rows claimed with `SELECT ... FOR UPDATE SKIP LOCKED`; attempts and
max attempts on the row; stale RUNNING jobs re-queued after 15 minutes. No Redis.

**Consequences.** One fewer service; job state is transactional with the data it produces. If
throughput ever matters, swap the claim function for a broker without changing the pipeline.
