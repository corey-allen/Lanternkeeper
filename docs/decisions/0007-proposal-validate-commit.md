# ADR-0007: Propose, validate, commit, then narrate

**Status:** Accepted (2026-10-29)

**Decision.** The model returns one `IntentProposal` (intent, target id, ability, difficulty
label, item, topic) and nothing computed. The server checks it against the scene's legal options
and the rules profile, maps it to a tool, and runs the tool inside a savepoint within a transaction
that locks the campaign row and checks `expected_version`. Narration is generated afterwards from
the committed outcome, with no transaction open, and guarded. Idempotency keys are unique per
campaign; failed provider calls leave a FAILED record whose key may be retried.

**Consequences.** Retries cannot double-apply; the model cannot change state or numbers. One
intent per player input; multi-step plans need clarification. A single DM agent, no multi-agent system.
