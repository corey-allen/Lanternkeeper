# ADR-0011: Typed modifiers and server-rolled character creation

**Status:** Accepted (2026-10-29). Plan 0002 step D1.

**Context.** Ancestry traits, class features, talents, items and spells all adjust rolls and derived
values in many small scoped ways. Character creation involves dice (stats, hit points, talents,
starting gold) that a client must not be able to pick or reroll at will.

**Decision.**
- One modifier type (`lk_rules.modifiers.Modifier`): target (attack, damage, extra damage dice,
  spellcasting, stat check, stat score, AC, HP, HP roll, gear slots, initiative, level-1 talent
  rolls), mode (bonus, advantage, disadvantage), amount (flat, from a stat modifier, optionally
  positive only, plus a per-level term), scope filters (attack kind, weapon or armor type, stat,
  tradition, spell, check tag, condition), and a source. `collect` returns the net bonus, the roll
  mode (advantage and disadvantage cancel), extra dice, and the breakdown for audit.
- Abilities the engine cannot resolve are `Feature`s with a kind (surprise immunity, limited use,
  granted spell, trained tasks, free item, learn from scrolls, narrative) and are stored, not guessed.
- Profile definitions are templates (`OptionDef`) with open choices (`$stat`, `$weapon_type`, ...),
  alternatives, choose-a-row, stat-point distribution, and explicit `deferred` / `unsupported` kinds.
  Unsupported or deferred results are recorded on the character as `pending`, never invented.
- Creation is a persisted draft: stats are rolled at start (manual entry only when the profile says
  so), the reroll condition is enforced, ancestry and class lock when hit points and talents are
  rolled, the roll-affecting choices are fingerprinted at that moment, and finalize validates all
  choices together and reports every problem. A draft cannot be discarded and restarted. Every roll
  is a `dice_roll` row. STAT_SCORE modifiers are folded into scores and kept for audit.
- Manual-entry profiles keep the one-shot `POST /campaigns/{id}/character`.

**Consequences.** Checks and combat do not yet apply character modifiers (step D2/D5). Spell
selection is pending until spellcasting (D7); weapon and armor proficiency until gear (D3).
