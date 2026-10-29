# Data model

Defined in `services/api/src/lk_api/models.py`; migration `services/api/migrations/versions/0001_initial_schema.py`.
Enums are stored as constrained `VARCHAR`s (check constraints), ids are UUIDs except `game_event.id`.

## Layers

```
SOURCE & INGESTION            ADVENTURE (approved, read-only in play)      CAMPAIGN (mutable)
source_document ─┬ source_page  adventure ─ adventure_entity ─┬ location     campaign ─ character ─┬ character_inventory
                 ├ source_passage ─────────── entity_evidence ├ npc_definition    │                └ character_condition
                 ├ ingestion_job              location_connection             ├ campaign_location_state
                 ├ extraction_candidate ─ candidate_evidence  adventure_edge  ├ campaign_discovery
                 └ ingestion_warning          (+ monster/clue/secret/quest/   ├ npc_instance ─ npc_memory
                                               encounter/story_beat/treasure/ ├ quest_state
RULES                                          hazard/faction definitions)    ├ encounter_state ─ encounter_participant
rules_profile ─ rule_reference                                                ├ world_clock, active_light, defeated_creature
             └ rules_coverage   house_rule (per campaign)                     ├ game_event (append-only), dice_roll
                                                                              └ action_record, campaign_snapshot
```

## Source and ingestion

| Table | Notes |
|-------|-------|
| `source_document` | Owner, adventure, display name (sanitised), opaque `storage_key`, `sha256`, size, pages, kind, `rights_status`, rights note, acknowledgement time, `visibility` (check: `PRIVATE`), `allow_remote_ai`, status. Unique `(owner_id, sha256)`. |
| `source_page` | Page number ≥ 1, extraction method (`TEXT`/`OCR`/`NONE`), raw and normalised text, char count, OCR confidence (null when the engine gives none), page size. |
| `source_passage` | Page, ordinal, carried-over heading, raw and normalised text, bbox (when available), method, `injection_flags`, generated `tsvector` with GIN index. |
| `ingestion_job` | Status, attempts/max, extractor, sanitised error code/message (never document text), lock time, stats. |
| `extraction_candidate` | Typed `data` validated by `ingestion/schemas.py`, status `PENDING/UNCERTAIN/APPROVED/REJECTED`, edited flag, reviewer note, promoted ids. |
| `candidate_evidence` | Candidate ↔ passage. |
| `ingestion_warning` | `OCR_USED`, `OCR_UNAVAILABLE`, `LOW_TEXT`, `DUPLICATE_ENTITY`, `CONFLICTING_FACT`, `BROKEN_REFERENCE`, `PROMPT_INJECTION_SUSPECTED`, `NO_EVIDENCE`, `PAGE_LIMIT`. |

## Adventure

`adventure_entity` is the hub (type, name, normalised name unique per adventure and type, summary,
visibility, source order, approver). One typed detail table per type, keyed by `entity_id`:
`location`, `npc_definition` (description, motivation, default disposition), `monster_definition`
(AC, HP ≥ 1, attack bonus, damage dice, initiative modifier: source facts), `clue_definition`
(content, reveal trigger, optional check ability and difficulty label), `secret_definition`,
`quest_definition`, `encounter_definition`, `story_beat`, `treasure_definition`,
`hazard_definition`, `faction_definition`.

- **Provenance**: `entity_evidence` links every entity to ≥ 1 passage (enforced in the review
  service; the eval harness checks the invariant). Connections and edges carry
  `evidence_passage_id` (not null).
- **Connectivity**: `location_connection` only (`from <> to`, unique pair, `bidirectional`,
  `hidden`, label). `adventure_edge` has typed `edge_type` (`CONTAINS`, `REVEALS`, `KNOWS`,
  `GUARDS`, `TRIGGERS`, `REQUIRES`, `HOSTILE_TO`, `ALLIED_WITH`, `LEADS_TO`, `HAS_REWARD`,
  `PRECEDES`, `BLOCKED_BY`, `MEMBER_OF`) and never encodes connectivity.
- **Visibility defaults**: locations, NPCs, creatures are `PLAYER_VISIBLE` when present; clues,
  secrets, quests, treasure, hazards, factions, encounters, story beats are `DM_ONLY` until the
  campaign records a discovery.

## Campaign

| Table | Notes |
|-------|-------|
| `campaign` | Owner, adventure (RESTRICT), rules profile, house-rule acceptance time, status, `version` (optimistic concurrency), current location. |
| `character` | One per campaign. Scores (check 1–30), HP (check `0 ≤ current ≤ max`, `max ≥ 1`), AC, level, rule refs used at creation. |
| `character` (creation fields) | Ancestry, alignment, deity, background, title, XP, gear slots, languages, base scores before level-1 increases, gold, pending (unsupported results). |
| `character_modifier` | Typed modifiers with source, scope and folded flag (STAT_SCORE already applied to scores). |
| `character_feature` | Non-numeric abilities (kind, uses per day/rest, tags). |
| `character_draft` | In-progress creation, validated by `lk_rules.creation.CreationDraft`; one per campaign, consumed on finalize. |
| `character_inventory` | Item key unique per character, quantity > 0, slots per item. |
| `character_condition` | Tracked condition keys with optional expiry minute. |
| `campaign_location_state` | Visited, first visit minute, searched. |
| `campaign_discovery` | Entity revealed to the player, with the event that revealed it. |
| `npc_instance` | Campaign disposition, location, status; unique per NPC definition. |
| `npc_memory` | Content, `learned_via` (`SOURCE_DEFINED`/`OBSERVED`/`TOLD`/`EVIDENCE`), event or passage (check: at least one), minute, reliability. |
| `quest_state` | Status with the event that last changed it. Transitions validated by `lk_rules.progression`. |
| `encounter_state` / `encounter_participant` | Round, turn index, status (partial unique index: one active encounter per campaign); participants with turn order, initiative, AC, HP (check), attack, damage, status. |
| `world_clock`, `active_light` | Minutes elapsed ≥ 0; lights with remaining minutes > 0 (expired lights are deleted with an event). |
| `defeated_creature` | Creatures defeated in this campaign (with the event). |
| `game_event` | Append-only; sequence unique per campaign; `campaign_version`; type, visibility, summary, payload, game minute, location. Trigger blocks UPDATE, and DELETE unless `SET LOCAL lanternkeeper.allow_event_purge = 'on'` (campaign deletion only). |
| `dice_roll` | Roll id, actor, purpose, expression, individual dice (kept flags), modifier, total, rule refs, roller kind, event, action. |
| `action_record` | Idempotency key (unique per campaign), request hash, input, expected/resulting version, status, proposal, validation, result, stored response, diagnostics, narration. |
| `campaign_snapshot` | Canonical state JSON + SHA-256; a checkpoint, never the source of truth. |

JSON columns hold only audit material (proposals, payloads, dice faces, snapshot copies) or data
validated by typed schemas (candidate data). Character statistics, inventory, encounters and
progression are relational columns.

## Rules

`rules_profile` mirrors every loaded profile, built-in or private (current definition, `content_hash`,
`is_private`); `rules_profile_version` stores each distinct version immutably, and
`campaign.rules_profile_version_id` pins a campaign to one of them (ADR-0010); `rule_reference` holds
per-rule status and citation; `rules_coverage` the computed mechanic status; `house_rule` records,
per campaign, which house-rule values the owner accepted and when.
