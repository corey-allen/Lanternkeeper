"""Built-in rules profiles.

``lk-dev-house-rules`` is an ORIGINAL, system-neutral set of house rules written
for development and testing. Its values are project decisions, not Shadowdark
rules, and a campaign must explicitly accept it (see ADR-0004).

``shadowdark-unverified`` is a placeholder. Every entry is UNVERIFIED, so every
mechanic raises UnverifiedRule until the owner verifies each value against their
own copy of the rules AND the licensing review clears its use
(docs/licensing-review.md). No Shadowdark values are recorded in this repository.
"""

from __future__ import annotations

from .profile import RULE_TYPES, RuleEntry, RulesProfile, RuleStatus

_HOUSE = "house rule: Lanternkeeper development profile v0 (ADR-0004); not an official game rule"


def _house(value: object, notes: str = "") -> RuleEntry:
    return RuleEntry(status=RuleStatus.HOUSE_RULE, source=_HOUSE, value=value, notes=notes)


DEV_HOUSE_RULES = RulesProfile(
    key="lk-dev-house-rules",
    name="Lanternkeeper Development House Rules",
    version="0.1.0",
    system="generic",
    description=(
        "Original, system-neutral rules used to exercise the engine end to end. "
        "Not Shadowdark. Values were chosen by the project for testing."
    ),
    requires_house_rule_acceptance=True,
    entries={
        "ability.score_range": _house({"min": 3, "max": 18}),
        "ability.modifier_table": _house(
            [
                {"min": 3, "max": 5, "modifier": -2},
                {"min": 6, "max": 8, "modifier": -1},
                {"min": 9, "max": 12, "modifier": 0},
                {"min": 13, "max": 15, "modifier": 1},
                {"min": 16, "max": 18, "modifier": 2},
            ]
        ),
        "check.difficulty": _house({"easy": 8, "normal": 11, "hard": 14, "extreme": 17}),
        "check.advantage_enabled": _house(True, "Advantage: roll 2d20 keep highest."),
        "check.natural_results": _house({"max_face": "none", "min_face": "none"},
                                        "Natural 20/1 have no special effect on checks."),
        "check.task_tags": _house([{"key": "climbing", "name": "Climbing"},
                                   {"key": "sneaking", "name": "Sneaking and hiding"},
                                   {"key": "forcing", "name": "Forcing or breaking things"},
                                   {"key": "noticing", "name": "Noticing things"}],
                                  "Task categories the DM model may assign to a check."),
        "stats.generation": _house({"method": "manual"}, "Scores are entered within ability.score_range."),
        "character.classes": _house(
            [{"key": "wayfarer", "name": "Wayfarer", "hit_die": "1d8"}],
            "One generic class with no talents.",
        ),
        "character.hit_points": _house({"level_1": "max_hit_die", "add_stat": "CON", "minimum": 1},
                                       "Level-1 HP = maximum of the hit die + CON modifier, minimum 1."),
        "character.armor_class": _house({"base": 10, "add_ability": "DEX"}),
        "items.catalog": _house(
            [
                {"key": "torch", "name": "Torch", "category": "light", "cost": {"amount": 1, "unit": "sp"},
                 "light": {"duration_minutes": 60, "radius": "near"}, "consumable": True},
                {"key": "rope", "name": "Rope (50 ft)", "cost": {"amount": 2, "unit": "sp"}},
                {"key": "rations", "name": "Rations", "cost": {"amount": 1, "unit": "sp"}, "quantity_per_slot": 2,
                 "consumable": True},
                {"key": "short_blade", "name": "Short blade", "category": "weapon", "cost": {"amount": 5, "unit": "gp"},
                 "weapon": {"kind": "melee", "ranges": ["close"], "damage": "1d6", "properties": ["finesse"]}},
                {"key": "walking_staff", "name": "Walking staff", "category": "weapon",
                 "cost": {"amount": 2, "unit": "sp"},
                 "weapon": {"kind": "melee", "ranges": ["close"], "damage": "1d4", "damage_two_handed": "1d6",
                            "properties": ["versatile"]}},
                {"key": "sling", "name": "Sling", "category": "weapon", "cost": {"amount": 1, "unit": "gp"},
                 "weapon": {"kind": "ranged", "ranges": ["near"], "damage": "1d4"}},
                {"key": "padded_coat", "name": "Padded coat", "category": "armor", "cost": {"amount": 8, "unit": "gp"},
                 "armor": {"kind": "body", "base_ac": 11, "add_dex": True}},
                {"key": "buckler", "name": "Buckler", "category": "shield", "cost": {"amount": 4, "unit": "gp"},
                 "armor": {"kind": "shield", "ac_bonus": 1, "occupies_hand": True}},
                {"key": "pack", "name": "Pack", "category": "container", "cost": {"amount": 1, "unit": "gp"},
                 "free_quantity": 1},
                {"key": "travel_kit", "name": "Travel kit", "category": "kit", "cost": {"amount": 3, "unit": "gp"},
                 "contents": [{"item_key": "pack", "quantity": 1}, {"item_key": "torch", "quantity": 2},
                              {"item_key": "rations", "quantity": 2}, {"item_key": "rope", "quantity": 1}]},
            ],
            "Original catalogue for development. Light duration is in tracked game minutes.",
        ),
        "items.currency": _house(
            {"denominations": [{"key": "gp", "name": "gold", "value": 100}, {"key": "sp", "name": "silver", "value": 10},
                               {"key": "cp", "name": "copper", "value": 1}],
             "coins_per_slot": 100, "free_coins": 100},
        ),
        "combat.attack_stats": _house({"melee": "STR", "ranged": "DEX", "either": ["STR", "DEX"]},
                                      "Finesse and thrown weapons use the better of STR and DEX."),
        "inventory.capacity": _house({"method": "fixed_slots", "slots": 10}),
        "time.action_minutes": _house(
            {"move": 10, "search": 10, "talk": 5, "check": 5, "use_item": 1, "combat_round": 1}
        ),
        "combat.initiative": _house({"ability": "DEX"}, "Initiative: d20 + ability modifier."),
        "combat.attack": _house(
            {"hit_when": "total_gte_armor_class", "minimum_damage": 1},
            "Attack: d20 + ability modifier (or creature attack bonus) vs AC. "
            "Critical hits and fumbles are UNSUPPORTED.",
        ),
        "combat.zero_hp": _house(
            {"character_outcome": "incapacitated", "creature_outcome": "defeated"},
            "Dying, death, and recovery from 0 HP are UNSUPPORTED.",
        ),
        "conditions.catalog": _house(
            ["incapacitated", "frightened", "poisoned"],
            "Conditions are tracked only; their mechanical effects are UNSUPPORTED.",
        ),
    },
)

_UNVERIFIED_SOURCE = (
    "Shadowdark RPG core rules: citation to be supplied by the owner after "
    "verification and licensing review (docs/licensing-review.md)"
)

SHADOWDARK_UNVERIFIED = RulesProfile(
    key="shadowdark-unverified",
    name="Shadowdark (unverified placeholder)",
    version="0.0.0",
    system="shadowdark",
    description=(
        "Placeholder for a Shadowdark rules profile. No values are recorded. Every "
        "mechanic is blocked until each rule is verified and licensing review passes."
    ),
    requires_house_rule_acceptance=False,
    entries={
        key: RuleEntry(status=RuleStatus.UNVERIFIED, source=_UNVERIFIED_SOURCE)
        for key in RULE_TYPES
    },
)

BUILTIN_PROFILES: dict[str, RulesProfile] = {
    p.key: p for p in (DEV_HOUSE_RULES, SHADOWDARK_UNVERIFIED)
}


def get_profile(key: str) -> RulesProfile:
    try:
        return BUILTIN_PROFILES[key]
    except KeyError as exc:
        raise KeyError(f"Unknown rules profile {key!r}") from exc
