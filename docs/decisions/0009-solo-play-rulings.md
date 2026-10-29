# ADR-0009: Solo-play rulings for Shadowdark

**Status:** Accepted by the owner (2026-10-29). Implemented per subsystem in plan 0002 step D;
each becomes a campaign or profile setting the owner can change.

**Context.** The rules leave several calls to the GM and assume a group of players. In a solo app
the model must not make these calls silently.

**Decisions.**

1. **Light timing.** Real time while the player is actively playing. The clock pauses when the app
   is closed or idle (default about 5 minutes), and model generation time is not charged. A visible
   timer is shown. The book's rounds-equivalence fallback remains a setting.
2. **Optional modes.** Standard rules first. Parameter-only modes (shorter light, initiative each
   round, fixed death timers, death at 0 HP) next. Mechanics-changing modes (luck-heavy, exploding
   damage, reduced rest recovery) after the base rules are stable.
3. **When to skip a roll.** The model classifies the task; the server grants automatic success only
   when the character has a feature granting it for that task category, or when there is neither
   time pressure nor consequence. Automatic success still costs game time, and that time drives
   random-encounter checks. The model cannot assert automatic success.
4. **GM-judgment rulings.** The model proposes a labelled ruling (e.g. background advantage); the
   player approves or rejects it and the ruling is logged. Categories can be set to auto-approve.
   Luck tokens: the model may nominate, the player confirms, the book's holding limit applies.
   Penance: the model proposes a task labelled as improvisation; the spell stays locked until the
   task is completed and a rest is taken.
5. **Group rules in solo play.** Group initiative and morale as written. Encounters above the book's
   difficulty guideline are flagged in the DM console, never auto-adjusted. A solo-difficulty house
   rule may be added later as an explicit option, off by default.
6. **Starting level.** Level 1 by default. A level-0 multi-character funnel is a later option,
   because it needs more than one player-controlled character per campaign.

**Consequences.** New typed concepts: task categories, auto-success features, rulings (proposed /
approved / rejected) with an audit trail, a session-activity clock, and play-mode flags.
