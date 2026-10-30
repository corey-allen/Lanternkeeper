# ADR-0012: Gear, coins and equipment

**Status:** Accepted (2026-10-29). Plan 0002 step D3.

**Decision.**
- `items.catalog` holds typed items: category, cost (amount + coin), slots per slot unit,
  quantity per slot, free quantity (e.g. the first backpack), and optional weapon, armor and light
  definitions. Bundles sold together are kits whose contents are added when acquired.
- `items.currency` defines denominations, coins per slot and free coins. Coins are stored per
  denomination in `character_coin` (typed rows, not JSON) and count toward gear slots.
- Making change is automatic: if exact coins are missing, the remainder is held in the fewest coins.
  This is a Lanternkeeper convenience, not a book rule.
- Proficiency comes from the class's weapon and armor lists and its shields flag. Equipping enforces
  it; buying does not.
- Equipped roles: `armor`, `shield`, `main_hand`, `both_hands`. A two-handed grip frees the shield.
  Off-hand weapons are not supported (no rule implemented).
- AC is recomputed from worn armor (or the unarmored rule), the shield, and `ARMOR_CLASS` modifiers,
  including those scoped to the worn armor or shield, whenever equipment changes.
- `combat.attack_stats` names the melee and ranged stats and the pair finesse/thrown weapons choose
  from; the engine uses the better one. Versatile weapons use the two-handed damage when wielded in
  both hands. Attacking with nothing wielded draws the first usable carried weapon (recorded).
- Equip, unequip and drop are direct, versioned player actions (`/campaigns/{id}/inventory/*`):
  campaign row locked, `expected_version` checked, event appended. They do not go through the model.

**Deferred.** Shops during play, selling, light fuel (lantern oil) and real-time light (D4),
encumbrance beyond slots, item conditions.
