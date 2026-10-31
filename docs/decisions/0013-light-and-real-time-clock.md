# ADR-0013: Light sources and the real-time play clock

**Status:** Accepted (2026-10-31). Implements plan 0002 step D4 and ADR-0009 #1.

**Context.** The core rules time light sources in real time, offer a rounds equivalence for tables that
cannot track real time, and give players two ways to handle more than one lit source. Total darkness
changes sight-based checks. The engine had one fixed model: every action cost profile-defined game
minutes, and every light kept its own timer.

**Decisions.**

1. **Timing modes.** A profile rule `time.light_timing` lists the timing modes it supports (`real_time`,
   `rounds`; the first is the default), the rounds-per-hour equivalence, and the multiple-light options.
   A campaign chooses its mode and option (`PUT /campaigns/{id}/time-settings`, a versioned, logged
   change). A profile without the rule keeps the earlier `action_minutes` model.
2. **What costs time.** Actions are classed as crawl actions, combat rounds, or "time passes"
   (searching a room, waiting). In real time, only "time passes" fast-forwards the clock; everything
   else is covered by elapsed real time. In rounds mode, each crawl action or combat round costs one
   round. "Time passes" durations always come from `time.action_minutes`.
3. **Real time is measured by the server, never reported by the client.** The play screen sends a
   heartbeat about every 30 seconds while it is visible and the player has been active within the
   campaign's idle pause (default 300 s). The server credits the gap since the previous heartbeat,
   but only if the gap is within the idle pause; a longer gap (app closed, idle, asleep) credits
   nothing. Submitting an action credits time up to the moment it was received and then pauses the
   clock, so model generation time is never charged; the screen resumes the clock when the response
   arrives.
4. **Credited time is held as pending seconds**, which are bookkeeping, not game state. They are
   flushed into game minutes (with a `TIME_ADVANCED` event and a version bump) when an action commits,
   or by a heartbeat once a deadline is reached (a light or condition would expire). This keeps the
   append-only log free of a time event every 30 seconds, while every state consequence of time is
   still an event. Displayed light time is `remaining_minutes × 60 − pending_seconds`.
5. **Multiple lights.** `ride_along`: the new source joins the current timer. `restart`: older sources
   go out and the timer restarts at the new source's full duration. Both consume the new source (or
   its fuel). The campaign's default is the profile's first listed option.
6. **Fuel.** A light item may name a fuel item (`light.fuel_item_key`); lighting it consumes one fuel
   instead of the light item itself.
7. **Total darkness.** A profile rule `vision.total_darkness` states the effect on sight-based checks.
   The model classifies whether a task needs sight (`needs_sight` on the proposal, a classification,
   not an outcome); searching is always a sight task. The server applies the effect.

**Deferred.** Encounter checks in darkness and while time passes (D6), campfires (D6 resting), light
mishaps (GM tables), the shorter-light play mode, darkness effects on attacks (D5).

**Consequences.** Light duration depends on how long the player actually plays, not on how many
actions they take. Leaving the app open and idle costs at most one idle-pause of light. A heartbeat
can change the campaign version (when a light burns out), so the client takes the version from every
heartbeat response.
