# ADR-0002: PostgreSQL for the adventure graph and search

**Status:** Accepted (2026-10-29)

**Context.** The graph is small per adventure and needs provenance and strong constraints.

**Decision.** Typed relational tables + typed `adventure_edge`; PostgreSQL full-text search over a
generated `tsvector` with a GIN index. No graph database. pgvector deferred until an evaluation
shows value, and blocked pending licensing review of derived artifacts.

**Consequences.** One datastore to back up. Retrieval quality depends on lexical search; filtering
by approval/visibility happens in SQL before ranking. Tests require PostgreSQL (embedded via pgserver).
