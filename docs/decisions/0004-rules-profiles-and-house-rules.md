# ADR-0004: Rules values live in profiles; no Shadowdark values shipped

**Status:** Accepted (2026-10-29), pending licensing review

**Context.** Mechanics must be deterministic and must not be invented. Implementing Shadowdark
rules is a licensing question (docs/licensing-review.md, Q1). A playable slice still needs
*some* rules.

**Decision.** The engine reads every value from a `RulesProfile`; each entry has status
`VERIFIED`, `HOUSE_RULE`, or `UNVERIFIED` and a source. Two built-in profiles:
`lk-dev-house-rules` (original, system-neutral values chosen by the project; a campaign must accept
it explicitly, recorded in `house_rule`) and `shadowdark-unverified` (no values; every mechanic
raises `UNVERIFIED_RULE`). Claims based on house rules are labelled `HOUSE_RULE`, never `VERIFIED_RULE`.

**Consequences.** The slice is playable without asserting anything about Shadowdark. Adding
Shadowdark support later means filling a profile with cited values after review, not changing
mechanics. Owner should review the dev values if they intend to keep playing with them.
