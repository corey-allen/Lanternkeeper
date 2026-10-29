# Rules coverage matrix

A mechanic is usable only when it is **implemented** in `packages/rules_engine` **and** every
rule value it needs is `VERIFIED` (cited source + licensing review passed) or an explicitly
accepted `HOUSE_RULE`. The live matrix is generated from code: `GET /api/v1/rules/profiles` and
the Rules coverage screen. This document records the reasoning and must be updated whenever a
mechanic or rule value changes.

No rule value in this repository is taken from Shadowdark. See `docs/licensing-review.md`.

## Profiles

| Profile | Purpose | Values |
|---------|---------|--------|
| `lk-dev-house-rules@0.1.0` | Original, system-neutral rules for development and tests. A campaign must accept it explicitly. | All `HOUSE_RULE`, chosen by the project. |
| `shadowdark-unverified@0.0.0` | Placeholder for a future Shadowdark profile. | All `UNVERIFIED`, no values. Every mechanic is blocked. |

## Mechanics

| Mechanic key | Engine module | Implemented | Needs rules | Dev profile | Shadowdark placeholder | Tests |
|---|---|---|---|---|---|---|
| `dice.roll` | `dice` | yes | — | AVAILABLE | AVAILABLE | parse/reject, audit record, seeded/secure rollers |
| `character.create` | `creation`, `character` | yes | stat generation, modifier table, classes, hit points, AC, capacity (+ optional ancestries, languages, alignments, deities, backgrounds, titles, starting gold) | AVAILABLE | BLOCKED | rolled stats and reroll rule, origin lock, HP advantage, talent tables (alternatives, choose-a-row, distribution, unsupported), languages, deity, roll-basis tamper check |
| `character.modifiers` | `modifiers` | yes | — | AVAILABLE | AVAILABLE | scopes, stat-derived and per-level amounts, advantage cancel |
| `character.spells_known` | — | **no** (recorded as pending) | — | UNSUPPORTED | UNSUPPORTED | pending entry |
| `check.ability` | `checks` | yes | difficulty table, modifier table | AVAILABLE | BLOCKED | success/failure, unknown label |
| `check.advantage` | `checks`, `dice` | yes | advantage flag | AVAILABLE | BLOCKED | keep-high/keep-low |
| `inventory.manage` | `inventory` | yes | items, capacity | AVAILABLE | BLOCKED | add/remove/consume, errors |
| `time.advance` | `clock` | yes | action minutes | AVAILABLE | BLOCKED | advance, bounds |
| `light.duration` | `clock` | yes | items (light duration) | AVAILABLE | BLOCKED | burn-down, expiry |
| `combat.initiative` | `combat` | yes | initiative ability | AVAILABLE | BLOCKED | ordering, tie-break |
| `combat.attack` | `combat` | yes | attack rule, zero-HP rule | AVAILABLE | BLOCKED | hit/miss, damage, victory/defeat, turn order |
| `conditions.track` | `progression` | yes (tracking only) | condition catalog | AVAILABLE | BLOCKED | apply/expire, unknown |
| `quest.transition` | `progression` | yes | — (campaign logic) | AVAILABLE | AVAILABLE | legal/illegal transitions |
| `conditions.effects` | — | **no** | — | UNSUPPORTED | UNSUPPORTED | typed error |
| `combat.critical` | — | **no** | — | UNSUPPORTED | UNSUPPORTED | — |
| `combat.morale` | — | **no** | — | UNSUPPORTED | UNSUPPORTED | — |
| `combat.dying` | — | **no** | — | UNSUPPORTED | UNSUPPORTED | 0 HP → incapacitated only |
| `rest.recover` | — | **no** | — | UNSUPPORTED | UNSUPPORTED | typed error via play loop |
| `advancement.level` | — | **no** | — | UNSUPPORTED | UNSUPPORTED | typed error |
| `spell.cast` | — | **no** | — | UNSUPPORTED | UNSUPPORTED | typed error via play loop |
| `class.abilities` | — | **no** | — | UNSUPPORTED | UNSUPPORTED | — |
| `ancestry` | — | **no** | — | UNSUPPORTED | UNSUPPORTED | — |
| `social.persuade` | — | **no** | — | UNSUPPORTED | UNSUPPORTED | typed error via play loop |
| `encounter.random` | — | **no** | — | UNSUPPORTED | UNSUPPORTED | — |
| `treasure.generate` | — | **no** | — | UNSUPPORTED | UNSUPPORTED | — |

**Not yet applied in play:** checks and combat do not use character modifiers yet (plan 0002 steps D2/D5).

## Dev house-rule values (not Shadowdark)

Ability scores 3–18; modifiers −2/−1/0/+1/+2 in bands 3–5/6–8/9–12/13–15/16–18; difficulty labels
easy 8, normal 11, hard 14, extreme 17; one class "Wayfarer" with 8 base HP (+CON mod, min 1);
AC 10 + DEX mod; 10 inventory slots; torch burns 60 tracked game minutes; action costs move 10,
search 10, talk 5, check 5, use item 1, combat round 1 minute; initiative d20 + DEX mod; attack
d20 + modifier ≥ AC, minimum damage 1; 0 HP: character incapacitated, creature defeated.

Monster statistics (AC, HP, attack bonus, damage) are **adventure source facts** taken from the
approved passage of the uploaded adventure, with provenance — not rules-engine values.

## Adding a mechanic or verified value (procedure)

1. Record here: source and page, edition/version, licensing status, exact behaviour, ambiguities.
2. Get licensing sign-off (docs/licensing-review.md, section 3).
3. Implement in `lk_rules` with typed inputs/outputs and unit tests for every branch.
4. Add/extend the `Mechanic` entry in `lk_rules/coverage.py`; never hard-code values in mechanics.
5. Add the tool and validation in the API; add an integration test through the play loop.
