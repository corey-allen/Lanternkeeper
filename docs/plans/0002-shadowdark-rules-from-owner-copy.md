# Increment 2: Shadowdark rules from the owner's own rulebook

Status: planned (2026-10-29). Owner decision: build the remaining mechanics against the real core
rules, sourced from the owner's own copy, kept private and uncommitted. Licensing question Q1
(may a private tool encode the rules?) remains open and is the owner's call; distribution stays
blocked (docs/licensing-review.md).

## Principles for this increment

- **Read before modelling.** For each subsystem, read the relevant rulebook pages first, then
  design the profile schema and mechanics. No schema is designed from memory.
- **Values stay private.** Rule values live only in `private/profiles/*.profile.json`, each entry
  with a page citation. The public repo holds the framework, schemas, and synthetic test values.
- **Public tests use synthetic values.** Unit tests in the repo exercise mechanics with invented
  numbers. Tests that check results against the rulebook's real values live in
  `private/conformance/` and run only on the owner's machine.
- **No rules text in the repo.** Docs name subsystems and schema shapes only. Quotes, paraphrases
  and page notes go in `private/notes/`.
- **Disclosure.** Reading pages in a Claude Code session sends their content to Anthropic. The owner
  confirms this per document before Claude reads it.
- `scripts/check_private_content.py` (also a pytest test) fails if anything under `private/`, any
  non-fixture PDF, or any exported profile is tracked.

## Private folder layout (gitignored)

```
private/rulebooks/     core rulebook PDF(s), owner's copy
private/adventures/    one or more owned modules or zines, for extraction tuning
private/profiles/      generated owner-verified profiles (*.profile.json)
private/notes/         subsystem maps, page references, quotes
private/conformance/   local-only conformance tests
```

## Steps

### A. Subsystem map (reading only, no code)
Read the table of contents and the rules chapters. Produce `private/notes/subsystem-map.md`: each
subsystem, its page range, the decisions it needs, and how it interacts with others. A public
summary of the categories only (no values) goes into `docs/rules-coverage-matrix.md`.

**Step A result (2026-10-29).** Sources supplied by the owner: the Shadowdark RPG core rulebook,
and the *Player's Guide* and *Game Master's Guide to the Western Reaches* (which consolidate Cursed
Scroll 1–6 and vary the core rules in places). The private map is `private/notes/subsystem-map.md`.
The core rules chapters (characters, magic, gameplay, the mechanical parts of the GM chapter,
monster attributes) have been read. Spell definitions and magic items have not been read in detail.

**What the current engine gets structurally wrong** (to fix in step D, not patch around):
- Gear capacity is a fixed number; it must derive from a stat with a floor plus class bonuses, and
  items need quantity-per-slot and free-carry allowances, plus multi-denomination currency.
- Classes have a flat base HP; they need a hit die per level, proficiency lists, fixed features,
  rolled talent tables whose results stack, and spells-known tables.
- There is no modifier system. Ancestry traits, talents, items and spells all grant typed
  modifiers (attack/damage bonuses scoped to a weapon type, stat increases, AC by armor type,
  advantage on a category of checks, extra damage dice, uses per day). This is the central
  missing piece.
- Advantage and disadvantage must cancel; natural 20/1 need explicit semantics; luck tokens
  (reroll your last roll) need a holding limit and an audit trail.
- Attacks choose the stat from melee/ranged/finesse, not from a per-weapon field; weapons need
  range bands, properties, and alternate two-handed damage.
- Initiative: monsters act on one group initiative, not individually; surprise grants a turn first.
- 0 HP means dying with a death timer, a recovery chance and stabilisation, not "incapacitated".
  Morale checks for enemies at a loss threshold.
- Light is timed in real time by default (with a rounds equivalence as fallback), with options for
  multiple light sources; total darkness changes both checks and encounter frequency.
- Time needs crawling rounds, combat rounds, and "time passes" fast-forwarding, each with its own
  random-encounter cadence driven by the area's danger level.
- Resting needs a ration and a duration, an interruption check, and full recovery including stat
  damage and lost spells.
- Spellcasting needs per-spell availability (lost on failure until rest), tradition stat, DC from
  tier, critical success and failure branches (mishap tables, penance), focus maintenance, and
  scrolls/wands.
- Advancement needs XP thresholds by level, XP reset, talent rolls on specific levels, rolled HP
  per level, and titles by class, alignment and level. XP comes from treasure quality.
- Optional play modes are rule toggles and belong in the profile as flags.

Further structural gaps (shapes only, no values):
- Monster definitions need movement, six stat modifiers, alignment, level, multiple and
  alternative attacks with ranges, and named abilities (including spells with their own DC).
- Items need cost, properties, and categories (weapons, armor, mounts, boats, siege, poisons).
- A hex-crawl travel subsystem (travel modes, terrain costs, visibility, weather, rations) and
  campaign-scale subsystems (renown, downtime, carousing, bastions, warbands).
- Class definitions need per-level talent tables and spell traditions with mishap tables.
- Setting content (regions, hex key, city districts, encounter tables) belongs in the adventure
  graph, which then needs hexes or regions as location kinds.

### B. Private profile loading
**Done (2026-10-29), see ADR-0010.** Campaigns pin to immutable versions; upgrades are explicit.
- Profiles loaded from `LK_RULES_PROFILE_DIR` (default `private/profiles` in dev, a mounted
  read-only volume in Compose), validated against `RULE_TYPES`, plus the built-in profiles.
- Each entry: `status: VERIFIED`, `source: "<book> p.<n>"`, owner's verification date.
- Campaigns record the profile key, version, and a content hash, so a later profile edit is
  detected rather than silently changing an ongoing campaign.
- Unit tests with a synthetic private profile; nothing from the book.

### C. Rulebook ingestion into rule candidates
- `document_kind = RULEBOOK`; same upload, extraction, passage and OCR path.
- New candidate types for rule values and tables, each citing evidence passages.
- Review screen for rule candidates. Approval writes or updates the private profile file, and the
  status becomes `VERIFIED` only on approval.
- Typing a value in by hand is the fallback (same citation requirement).

### D. Subsystems, in dependency order
For each: read pages → extend `RULE_TYPES` and the engine → synthetic unit tests → private profile
entries → private conformance tests → API tool and play-loop integration → coverage matrix update.

1. Character creation: stat generation, modifiers, ancestries, classes, hit points, starting gear.
   **Done for level 1 (2026-10-29, ADR-0011)** except spell selection (D7), gear purchase and
   proficiencies (D3), applying modifiers in play (D2/D5), level-up (D8). The owner's values are in
   `private/profiles/shadowdark-core.profile.json` (UNVERIFIED until reviewed with
   `scripts/review_profile.py`); private conformance tests in `private/conformance/`.
2. Checks: difficulty, advantage and disadvantage, and any special-case rolls.
   **Done (2026-10-29):** character modifiers in checks (scoped by stat and task category), advantage/
   disadvantage cancelling across sources, natural results from the profile, task categories the model
   must pick from (`check.task_tags`), trained-task automatic success outside combat (ADR-0009 #3).
   Deferred: contested checks (with stealth, D6), luck tokens and approved rulings (own step).
3. Gear and inventory: slot capacity rules, item catalogue structure, currency.
   **Done (2026-10-29, ADR-0012):** typed catalogue with kits, coin slots, starting-gear purchase in the
   creation draft, proficiency, equip/unequip/drop, AC from worn armor, attack stat and versatile damage
   from the wielded weapon. Deferred: shops in play, light fuel, real-time light (D4).
4. Light and time: light duration semantics. If the book times light in real time, add a
   real-time clock mode alongside game time, with pause/resume semantics as an explicit decision.
5. Combat: initiative, attacks, damage, critical results, morale, dying and death.
6. Exploration: crawling rounds, danger level and encounter checks, resting and recovery.
7. Spellcasting: casting checks, failure consequences, spell definitions (structure only in the
   repo; spell content in the private profile).
8. Advancement: XP, levelling, talents.

Each subsystem is its own reviewable change. Where the book leaves something to the GM, the engine
asks for an explicit house-rule decision instead of choosing.

### E. Adventure extraction against a real layout
- Owner supplies one module or zine in `private/adventures/`.
- Study its layout and stat-block format. Build a matching heuristic parser and/or model-assisted
  extraction with a local model (no remote transmission).
- Add an original synthetic fixture that imitates the layout (structure only, invented content)
  so the parser is covered by public tests.

### Design questions raised by the rules (solo play)
1. Real-time light versus game-time light, and what happens while the app is closed.
2. Which optional play modes to support first.
3. When the DM layer should skip a roll: the rules often grant automatic success for trained tasks.
4. GM-judgment rulings (background advantage, penance, luck awards): an explicit, auditable ruling
   mechanism instead of letting the model decide silently.
5. Group-oriented rules in a solo game: group initiative, morale, encounter balance.
6. Starting at level 0 versus level 1 for a single character.

## Decisions needed from the owner

| When | Decision |
|------|----------|
| Before step A | Confirm Claude may read `private/rulebooks/<file>` (content is sent to Anthropic). |
| Step D4 | How real-time light should behave when the app is closed or the session is paused. |
| Step D, per subsystem | Any table rule left to GM discretion: house rule, or ask each time. |
| Step E | Which module to tune extraction on; whether a local model (e.g. Ollama) is available. |
