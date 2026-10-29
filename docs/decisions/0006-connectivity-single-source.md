# ADR-0006: Location connectivity has one source of truth

**Status:** Accepted (2026-10-29)

**Decision.** Connectivity lives only in `location_connection` (bidirectional and hidden flags,
evidence). `adventure_edge` deliberately has no CONNECTS_TO type. A future map layer stores only
presentation data keyed by location id.

**Consequences.** Maps can be added without migrating or duplicating connectivity.
