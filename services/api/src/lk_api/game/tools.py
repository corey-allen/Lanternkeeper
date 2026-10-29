"""Validated game tools.

Read tools never mutate. Mutating tools run only inside the orchestrator's
commit transaction, with the campaign row locked, and every one of them checks
its preconditions and records events and dice through ``ToolContext``. The model
never calls these directly: it can only produce an ``IntentProposal`` that the
orchestrator validates and maps to a tool.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from lk_rules import Ability, RulesProfile
from lk_rules.character import ability_modifier
from lk_rules.checks import CheckRequest, CheckResult, resolve_check
from lk_rules.clock import ActiveLight as RulesLight
from lk_rules.clock import ClockState, advance, light_source
from lk_rules.combat import Combatant, CombatantStatus, Encounter, EncounterStatus, next_turn, resolve_attack, start_encounter
from lk_rules.dice import RollRecord, Roller
from lk_rules.inventory import remove_item
from lk_rules.profile import InitiativeRule
from lk_rules.progression import QuestStatus as RQuest
from lk_rules.progression import TrackedCondition, expire_conditions, transition_quest

from ..models import (
    ActiveLight,
    AdventureEdge,
    AdventureEntity,
    Campaign,
    CampaignDiscovery,
    CampaignLocationState,
    Character,
    CharacterCondition,
    CharacterInventory,
    ClueDefinition,
    DefeatedCreature,
    DiceRoll,
    EdgeType,
    EncounterParticipant,
    EncounterState,
    EntityType,
    GameEvent,
    LearnedVia,
    NPCDefinition,
    NPCInstance,
    NPCMemory,
    NpcStatus,
    ParticipantKind,
    QuestState,
    QuestStatus,
    RevealTrigger,
    Visibility,
    WorldClock,
)
from ..models import EncounterStatus as DBEncounterStatus
from . import state as st


class ToolRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class ToolContext:
    session: Session
    campaign: Campaign
    character: Character
    profile: RulesProfile
    roller: Roller
    action_id: uuid.UUID | None
    events: list[GameEvent] = field(default_factory=list)
    rolls: list[DiceRoll] = field(default_factory=list)
    outcome: list[str] = field(default_factory=list)  # player-safe, deterministic summary lines
    rule_refs: list[str] = field(default_factory=list)
    _seq: int | None = None

    @property
    def new_version(self) -> int:
        return self.campaign.version + 1

    def minute(self) -> int:
        wc = self.session.get(WorldClock, self.campaign.id)
        return wc.minutes_elapsed if wc else 0

    def event(self, event_type: str, summary: str, payload: dict[str, Any], *,
              visibility: Visibility = Visibility.PLAYER_VISIBLE, witnessed: bool = False) -> GameEvent:
        if self._seq is None:
            self._seq = self.session.scalar(select(func.coalesce(func.max(GameEvent.sequence), 0))
                                            .where(GameEvent.campaign_id == self.campaign.id)) or 0
        self._seq += 1
        ev = GameEvent(campaign_id=self.campaign.id, sequence=self._seq, campaign_version=self.new_version,
                       event_type=event_type, visibility=visibility, summary=summary[:500], payload=payload,
                       action_id=self.action_id, game_minute=self.minute(),
                       location_id=self.campaign.current_location_id)
        self.session.add(ev)
        self.session.flush()
        self.events.append(ev)
        if witnessed:
            # Only NPCs present at the location can learn of what happened here.
            for inst, _ in st.npcs_present(self.session, self.campaign):
                self.session.add(NPCMemory(npc_instance_id=inst.id, content=f"Witnessed: {summary}"[:2000],
                                           learned_via=LearnedVia.OBSERVED, event_id=ev.id,
                                           learned_at_minute=ev.game_minute))
        return ev

    def record_roll(self, rec: RollRecord, event: GameEvent | None = None) -> None:
        row = DiceRoll(id=uuid.UUID(rec.roll_id), campaign_id=self.campaign.id, action_id=self.action_id,
                       event_id=event.id if event else None, actor=rec.actor, purpose=rec.purpose,
                       expression=rec.expression, dice=[d.model_dump() for d in rec.dice], modifier=rec.modifier,
                       total=rec.total, rule_refs=list(rec.rule_refs), roller_kind=rec.roller_kind)
        self.session.add(row)
        self.rolls.append(row)
        self.rule_refs.extend(rec.rule_refs)


# =============================================================================
# Read-only tools (also used to assemble model context)
# =============================================================================


def get_character_sheet(session: Session, campaign: Campaign) -> dict[str, Any]:
    return st.player_view(session, campaign)["character"] or {}


def get_current_scene(session: Session, campaign: Campaign) -> dict[str, Any]:
    v = st.player_view(session, campaign)
    return {k: v[k] for k in ("location", "exits", "npcs", "enemies", "encounter", "clock")}


def get_quest_state(session: Session, campaign: Campaign) -> list[dict[str, Any]]:
    return st.player_view(session, campaign)["quests"]


def get_recent_game_events(session: Session, campaign: Campaign, limit: int = 10) -> list[dict[str, Any]]:
    return st.player_view(session, campaign)["recent_events"][:limit]


def get_npc_knowledge(session: Session, campaign: Campaign, npc_entity_id: uuid.UUID) -> list[dict[str, Any]]:
    """Only this NPC's own memories. Never other NPCs' memories or the global event log."""
    inst = session.scalar(select(NPCInstance).where(NPCInstance.campaign_id == campaign.id,
                                                    NPCInstance.npc_entity_id == npc_entity_id))
    if inst is None:
        return []
    mems = session.scalars(select(NPCMemory).where(NPCMemory.npc_instance_id == inst.id)
                           .order_by(NPCMemory.created_at)).all()
    return [{"content": m.content, "learned_via": m.learned_via.value, "event_id": m.event_id,
             "passage_id": str(m.passage_id) if m.passage_id else None, "minute": m.learned_at_minute} for m in mems]


def lookup_supported_rule(profile: RulesProfile, rule_key: str) -> dict[str, Any]:
    entry = profile.entries.get(rule_key)
    if entry is None:
        return {"rule_key": rule_key, "status": "MISSING"}
    return {"rule_key": rule_key, "status": entry.status.value, "source": entry.source,
            "value": entry.value if entry.status.value != "UNVERIFIED" else None, "notes": entry.notes}


READ_TOOLS = {
    "get_campaign_snapshot": st.canonical_state,
    "get_current_scene": get_current_scene,
    "get_character_sheet": get_character_sheet,
    "get_quest_state": get_quest_state,
    "get_recent_game_events": get_recent_game_events,
    "get_npc_knowledge": get_npc_knowledge,
    "lookup_supported_rule": lookup_supported_rule,
    # get_location_details / search_adventure_sources live in lk_api.retrieval
}


# =============================================================================
# Mutating tools
# =============================================================================


def require_can_act(ctx: ToolContext) -> None:
    if ctx.session.scalar(select(CharacterCondition.id).where(
            CharacterCondition.character_id == ctx.character.id,
            CharacterCondition.condition_key == "incapacitated")):
        raise ToolRejected("CHARACTER_INCAPACITATED", f"{ctx.character.name} is incapacitated. Recovery from "
                           "0 HP is not supported by the active rules profile.")


def require_not_in_combat(ctx: ToolContext) -> None:
    if st.active_encounter(ctx.session, ctx.campaign):
        raise ToolRejected("IN_COMBAT", "You are in combat. Only attacks are supported until it ends "
                           "(fleeing is not implemented).")


def advance_game_time(ctx: ToolContext, minutes: int, reason: str) -> None:
    """Advance the clock; also burns down lights (consume_light_duration) and expires conditions."""
    wc = ctx.session.get(WorldClock, ctx.campaign.id, with_for_update=True)
    assert wc is not None
    rows = list(ctx.session.scalars(select(ActiveLight).where(ActiveLight.campaign_id == ctx.campaign.id)))
    before = ClockState(minutes_elapsed=wc.minutes_elapsed, lights=tuple(
        RulesLight(light_id=str(r.id), item_key=r.item_key, remaining_minutes=r.remaining_minutes) for r in rows))
    step = advance(before, minutes)
    wc.minutes_elapsed = step.after.minutes_elapsed
    remaining = {li.light_id: li.remaining_minutes for li in step.after.lights}
    for r in rows:
        if str(r.id) in remaining:
            r.remaining_minutes = remaining[str(r.id)]
        else:
            ctx.session.delete(r)
    ctx.session.flush()
    ctx.event("TIME_ADVANCED", f"{minutes} minutes passed ({reason})",
              {"minutes": minutes, "reason": reason, "now": wc.minutes_elapsed})
    for light in step.expired_lights:
        ctx.event("LIGHT_EXPIRED", f"Your {light.item_key} burned out", {"item_key": light.item_key})
        ctx.outcome.append(f"Your {light.item_key} burned out.")
        if not step.after.is_lit:
            ctx.outcome.append("You are now in darkness.")
    conds = list(ctx.session.scalars(select(CharacterCondition).where(CharacterCondition.character_id == ctx.character.id)))
    _, expired = expire_conditions(tuple(TrackedCondition(key=c.condition_key, expires_at_minute=c.expires_at_minute)
                                         for c in conds), wc.minutes_elapsed)
    for c in conds:
        if any(e.key == c.condition_key for e in expired):
            ctx.session.delete(c)
            ctx.event("CONDITION_ENDED", f"{c.condition_key} ended", {"condition": c.condition_key})


def reveal_entity(ctx: ToolContext, entity: AdventureEntity, how: str) -> bool:
    """Record a discovery (reveal_clue). Follows REVEALS edges. Returns False if already known."""
    if ctx.session.get(CampaignDiscovery, (ctx.campaign.id, entity.id)):
        return False
    ev = ctx.event("ENTITY_REVEALED", f"Discovered {entity.entity_type.value.lower()}: {entity.name}",
                   {"entity_id": str(entity.id), "entity_type": entity.entity_type.value, "how": how},
                   witnessed=True)
    ctx.session.add(CampaignDiscovery(campaign_id=ctx.campaign.id, entity_id=entity.id, event_id=ev.id))
    ctx.session.flush()
    if entity.entity_type == EntityType.CLUE:
        clue = ctx.session.get(ClueDefinition, entity.id)
        ctx.outcome.append(f"You discovered a clue, {entity.name}: {clue.content if clue else ''}")
    elif entity.entity_type == EntityType.SECRET:
        ctx.outcome.append(f"You uncovered a secret: {entity.name}.")
    elif entity.entity_type == EntityType.LOCATION:
        ctx.outcome.append(f"You found a way to {entity.name}.")
    for edge in ctx.session.scalars(select(AdventureEdge).where(AdventureEdge.source_entity_id == entity.id,
                                                                AdventureEdge.edge_type == EdgeType.REVEALS)):
        target = ctx.session.get(AdventureEntity, edge.target_entity_id)
        if target is None:
            continue
        if target.entity_type == EntityType.QUEST:
            update_quest_progress(ctx, target, QuestStatus.DISCOVERED)
        else:
            reveal_entity(ctx, target, f"revealed by {entity.name}")
    return True


def update_quest_progress(ctx: ToolContext, quest: AdventureEntity, target: QuestStatus) -> None:
    qs = ctx.session.get(QuestState, (ctx.campaign.id, quest.id))
    if qs is None:
        raise ToolRejected("UNKNOWN_QUEST", "Quest is not part of this campaign")
    new = transition_quest(RQuest(qs.status.value), RQuest(target.value))
    ev = ctx.event("QUEST_UPDATED", f"Quest '{quest.name}' is now {new.value.lower()}",
                   {"quest_id": str(quest.id), "from": qs.status.value, "to": new.value})
    qs.status = QuestStatus(new.value)
    qs.updated_event_id = ev.id
    ctx.outcome.append(f"Quest {new.value.lower()}: {quest.name}.")


def _clues_at(ctx: ToolContext, trigger: RevealTrigger) -> list[tuple[AdventureEntity, ClueDefinition]]:
    discovered = st.discovered_ids(ctx.session, ctx.campaign.id)
    out = []
    for e in st.contained(ctx.session, ctx.campaign.current_location_id, EntityType.CLUE):
        clue = ctx.session.get(ClueDefinition, e.id)
        if clue and clue.reveal_trigger == trigger and e.id not in discovered:
            out.append((e, clue))
    return out


def enter_location(ctx: ToolContext, location_id: uuid.UUID) -> None:
    ctx.campaign.current_location_id = location_id
    loc = ctx.session.get(AdventureEntity, location_id)
    ls = ctx.session.get(CampaignLocationState, (ctx.campaign.id, location_id))
    first = ls is None or not ls.visited
    if ls is None:
        ls = CampaignLocationState(campaign_id=ctx.campaign.id, location_id=location_id)
        ctx.session.add(ls)
    ls.visited = True
    if ls.first_visited_minute is None:
        ls.first_visited_minute = ctx.minute()
    ctx.session.flush()
    ctx.event("LOCATION_ENTERED", f"Entered {loc.name}", {"location_id": str(location_id), "first_visit": first},
              witnessed=True)
    ctx.outcome.append(f"You are in {loc.name}.")
    for e, _ in _clues_at(ctx, RevealTrigger.ON_ENTER):
        reveal_entity(ctx, e, "noticed on entering")


def move_to_location(ctx: ToolContext, target_id: str) -> None:
    require_can_act(ctx)
    require_not_in_combat(ctx)
    discovered = st.discovered_ids(ctx.session, ctx.campaign.id)
    legal = {x["location_id"]: x for x in st.exits(ctx.session, ctx.campaign, discovered)}
    if target_id not in legal:
        raise ToolRejected("NO_SUCH_EXIT", "There is no known way from here to that place.")
    minutes, ref = ctx.profile.action_minutes("move")
    ctx.rule_refs.append(str(ref))
    advance_game_time(ctx, minutes, "travel")
    enter_location(ctx, uuid.UUID(target_id))


def request_ability_check(ctx: ToolContext, ability: str, difficulty: str, purpose: str) -> CheckResult:
    require_can_act(ctx)
    try:
        ab = Ability(ability.upper())
    except ValueError as exc:
        raise ToolRejected("INVALID_ABILITY", f"Unknown ability {ability!r}") from exc
    result = resolve_check(CheckRequest(ability=ab, difficulty=difficulty, purpose=purpose),
                           score=st.scores(ctx.character).score(ab), actor=ctx.character.name,
                           profile=ctx.profile, roller=ctx.roller)
    word = "succeeded" if result.success else "failed"
    ev = ctx.event("CHECK_RESOLVED",
                   f"{ab.value} check ({difficulty}) {word}: rolled {result.roll.total} vs {result.target}",
                   {"ability": ab.value, "difficulty": difficulty, "target": result.target,
                    "total": result.roll.total, "success": result.success, "purpose": purpose,
                    "roll_id": result.roll.roll_id}, witnessed=True)
    ctx.record_roll(result.roll, ev)
    ctx.outcome.append(f"{ab.value} check ({difficulty}, target {result.target}): rolled {result.roll.total} "
                       f"- {word}.")
    return result


def search_location(ctx: ToolContext) -> None:
    require_can_act(ctx)
    require_not_in_combat(ctx)
    minutes, ref = ctx.profile.action_minutes("search")
    ctx.rule_refs.append(str(ref))
    advance_game_time(ctx, minutes, "searching")
    ls = ctx.session.get(CampaignLocationState, (ctx.campaign.id, ctx.campaign.current_location_id))
    if ls:
        ls.searched = True
    found = False
    for e, clue in _clues_at(ctx, RevealTrigger.ON_SEARCH):
        if clue.check_ability and clue.check_difficulty:
            if not request_ability_check(ctx, clue.check_ability, clue.check_difficulty, f"search: {e.name}").success:
                continue
        found = reveal_entity(ctx, e, "found by searching") or found
    if not found:
        ctx.outcome.append("Your search turns up nothing new.")


def record_npc_interaction(ctx: ToolContext, npc_entity_id: str, topic: str) -> AdventureEntity:
    require_can_act(ctx)
    require_not_in_combat(ctx)
    present = {str(e.id): (i, e) for i, e in st.npcs_present(ctx.session, ctx.campaign)}
    if npc_entity_id not in present:
        raise ToolRejected("NPC_NOT_PRESENT", "That person is not here.")
    inst, npc = present[npc_entity_id]
    minutes, ref = ctx.profile.action_minutes("talk")
    ctx.rule_refs.append(str(ref))
    advance_game_time(ctx, minutes, "conversation")
    ev = ctx.event("NPC_INTERACTION", f"{ctx.character.name} spoke with {npc.name}",
                   {"npc_entity_id": npc_entity_id, "topic": topic[:300]})
    # The addressed NPC learns what was said; bystanders learn only that a conversation happened.
    ctx.session.add(NPCMemory(npc_instance_id=inst.id, content=f"{ctx.character.name} said: {topic[:300]}",
                              learned_via=LearnedVia.TOLD, event_id=ev.id, learned_at_minute=ev.game_minute))
    for other, oe in present.values():
        if other.id != inst.id:
            ctx.session.add(NPCMemory(npc_instance_id=other.id, learned_via=LearnedVia.OBSERVED, event_id=ev.id,
                                      content=f"Saw {ctx.character.name} talking with {npc.name}",
                                      learned_at_minute=ev.game_minute))
    ctx.outcome.append(f"You speak with {npc.name}.")
    for edge in ctx.session.scalars(select(AdventureEdge).where(AdventureEdge.source_entity_id == npc.id,
                                                                AdventureEdge.edge_type == EdgeType.KNOWS)):
        target = ctx.session.get(AdventureEntity, edge.target_entity_id)
        clue = ctx.session.get(ClueDefinition, edge.target_entity_id) if target else None
        if target and clue and clue.reveal_trigger == RevealTrigger.ON_TALK:
            reveal_entity(ctx, target, f"told by {npc.name}")
    return npc


def use_item(ctx: ToolContext, item_key: str) -> None:
    require_can_act(ctx)
    rows = st.inventory_rows(ctx.session, ctx.character)
    row = next((r for r in rows if r.item_key == item_key), None)
    if row is None:
        raise ToolRejected("ITEM_NOT_IN_INVENTORY", "You do not have that item.")
    item_def, ref = ctx.profile.item(item_key)
    ctx.rule_refs.append(str(ref))
    if item_def is None or item_def.light is None:
        raise ToolRejected("UNSUPPORTED_MECHANIC",
                           f"Using {row.name} has no effect defined by the active rules profile.")
    inv = remove_item(st.inventory_model(rows), item_key, 1)
    left = inv.quantity_of(item_key)
    if left:
        row.quantity = left
    else:
        ctx.session.delete(row)
    clock_state = light_source(ClockState(), item_key=item_key, light_id="new", profile=ctx.profile)
    light = ActiveLight(campaign_id=ctx.campaign.id, item_key=item_key, lit_at_minute=ctx.minute(),
                        remaining_minutes=clock_state.lights[0].remaining_minutes)
    ctx.session.add(light)
    ctx.session.flush()
    ctx.event("ITEM_USED", f"Lit a {row.name} ({light.remaining_minutes} minutes of light)",
              {"item_key": item_key, "consumed": 1, "remaining_quantity": left,
               "light_minutes": light.remaining_minutes}, witnessed=True)
    ctx.outcome.append(f"You light a {row.name.lower()}; it will burn for {light.remaining_minutes} game minutes. "
                       f"{left} left.")
    minutes, ref = ctx.profile.action_minutes("use_item")
    ctx.rule_refs.append(str(ref))
    advance_game_time(ctx, minutes, "using an item")


# ---- combat ------------------------------------------------------------------


def _weapon(ctx: ToolContext):
    for r in st.inventory_rows(ctx.session, ctx.character):
        item_def, _ = ctx.profile.item(r.item_key)
        if item_def and item_def.weapon:
            return item_def
    return None


def _cid(p: EncounterParticipant) -> str:
    """Combatant ids used by the rules engine: 'pc' for the character, the entity id for creatures."""
    return "pc" if p.kind == ParticipantKind.CHARACTER else str(p.entity_id)


def _to_rules(enc: EncounterState) -> Encounter:
    return Encounter(round=enc.round, turn_index=enc.turn_index, status=EncounterStatus(enc.status.value),
                     combatants=tuple(Combatant(
                         combatant_id=_cid(p), name=p.name, side=p.side, armor_class=p.armor_class, hp=p.hp,
                         max_hp=p.max_hp, attack_modifier=p.attack_modifier, damage=p.damage,
                         initiative_modifier=0, status=CombatantStatus(p.status), initiative=p.initiative)
                         for p in enc.participants))


def _sync(ctx: ToolContext, enc: EncounterState, model: Encounter) -> None:
    enc.round, enc.turn_index = model.round, model.turn_index
    enc.status = DBEncounterStatus(model.status.value)
    for p in enc.participants:
        c = model.get(_cid(p))
        p.hp, p.status = c.hp, c.status.value
        if p.kind == ParticipantKind.CHARACTER:
            ctx.character.hp_current = c.hp


def start_encounter_tool(ctx: ToolContext) -> EncounterState:
    enemies = st.enemies_present(ctx.session, ctx.campaign)
    if not enemies:
        raise ToolRejected("NO_ENEMIES", "There is nothing here to fight.")
    weapon = _weapon(ctx)
    if weapon is None:
        raise ToolRejected("NO_WEAPON", "You have no weapon; unarmed attacks are not defined by the active "
                           "rules profile.")
    init_rule, init_ref = ctx.profile.get("combat.initiative", InitiativeRule)
    scores = st.scores(ctx.character)
    pc_init, _ = ability_modifier(scores.score(init_rule.ability), ctx.profile)
    pc_atk, _ = ability_modifier(scores.score(weapon.weapon.attack_ability), ctx.profile)
    combatants = [Combatant(combatant_id="pc", name=ctx.character.name, side="player",
                            armor_class=ctx.character.armor_class, hp=ctx.character.hp_current,
                            max_hp=ctx.character.hp_max, attack_modifier=pc_atk, damage=weapon.weapon.damage,
                            initiative_modifier=pc_init)]
    for e, m in enemies:
        combatants.append(Combatant(combatant_id=str(e.id), name=e.name, side="enemy", armor_class=m.armor_class,
                                    hp=m.hit_points, max_hp=m.hit_points, attack_modifier=m.attack_bonus,
                                    damage=m.damage, initiative_modifier=m.initiative_modifier))
    outcome = start_encounter(combatants, profile=ctx.profile, roller=ctx.roller)
    enc = EncounterState(campaign_id=ctx.campaign.id, location_id=ctx.campaign.current_location_id,
                         status=DBEncounterStatus.ACTIVE, round=1, turn_index=0)
    ctx.session.add(enc)
    ctx.session.flush()
    for order, c in enumerate(outcome.encounter.combatants):
        is_pc = c.combatant_id == "pc"
        enc.participants.append(EncounterParticipant(
            kind=ParticipantKind.CHARACTER if is_pc else ParticipantKind.MONSTER,
            character_id=ctx.character.id if is_pc else None, entity_id=None if is_pc else uuid.UUID(c.combatant_id),
            name=c.name, side=c.side, turn_order=order, initiative=c.initiative or 0, armor_class=c.armor_class,
            hp=c.hp, max_hp=c.max_hp, attack_modifier=c.attack_modifier, damage=c.damage, status=c.status.value))
    ctx.session.flush()
    ev = ctx.event("ENCOUNTER_STARTED", "Combat began: " + ", ".join(c.name for c in outcome.encounter.combatants),
                   {"encounter_id": str(enc.id), "order": [c.name for c in outcome.encounter.combatants]},
                   witnessed=True)
    names = {c.combatant_id: c.name for c in outcome.encounter.combatants}
    for rec in outcome.rolls:
        ctx.record_roll(rec.model_copy(update={"actor": names.get(rec.actor, rec.actor)}), ev)
    ctx.outcome.append("Combat begins. Turn order: " + ", ".join(
        f"{c.name} ({c.initiative})" for c in outcome.encounter.combatants) + ".")
    return enc


def _attack(ctx: ToolContext, enc: EncounterState, model: Encounter, attacker_id: str, target_id: str) -> Encounter:
    out = resolve_attack(model, attacker_id=attacker_id, target_id=target_id, profile=ctx.profile, roller=ctx.roller)
    attacker, target = model.get(attacker_id), model.get(target_id)
    summary = (f"{attacker.name} hits {target.name} for {out.damage} damage" if out.hit
               else f"{attacker.name} misses {target.name}")
    ev = ctx.event("ATTACK_RESOLVED", summary, {
        "attacker": attacker.name, "target": target.name, "hit": out.hit, "attack_total": out.attack_roll.total,
        "target_ac": out.target_armor_class, "damage": out.damage, "target_hp_after": out.target_hp_after,
        "target_status": out.target_status.value}, witnessed=True)
    ctx.record_roll(out.attack_roll.model_copy(update={"actor": attacker.name}), ev)
    if out.damage_roll:
        ctx.record_roll(out.damage_roll.model_copy(update={"actor": attacker.name}), ev)
    ctx.outcome.append(f"{summary} (attack roll {out.attack_roll.total} vs AC {out.target_armor_class}).")
    if out.target_status != CombatantStatus.ACTIVE:
        ctx.outcome.append(f"{target.name} is {out.target_status.value}.")
    return out.encounter


def resolve_attack_tool(ctx: ToolContext, target_id: str) -> None:
    require_can_act(ctx)
    enc = st.active_encounter(ctx.session, ctx.campaign)
    if enc is None:
        if target_id not in {str(e.id) for e, _ in st.enemies_present(ctx.session, ctx.campaign)}:
            raise ToolRejected("INVALID_TARGET", "That is not a valid target here.")
        enc = start_encounter_tool(ctx)
    model = _to_rules(enc)
    pc = next(c for c in model.combatants if c.side == "player")
    if target_id not in {c.combatant_id for c in model.combatants if c.side == "enemy" and c.can_act}:
        raise ToolRejected("INVALID_TARGET", "That is not a valid target in this fight.")
    rounds_before = model.round
    # Enemies who act before the player in initiative order take their turns first.
    while model.status == EncounterStatus.ACTIVE and model.current.side == "enemy":
        model = _attack(ctx, enc, model, model.current.combatant_id, pc.combatant_id)
        model = next_turn(model)
    if model.status == EncounterStatus.ACTIVE:
        model = _attack(ctx, enc, model, pc.combatant_id, target_id)
        model = next_turn(model)
    while model.status == EncounterStatus.ACTIVE and model.current.side == "enemy":
        model = _attack(ctx, enc, model, model.current.combatant_id, pc.combatant_id)
        model = next_turn(model)
    _sync(ctx, enc, model)
    rounds = max(1, model.round - rounds_before)
    minutes, ref = ctx.profile.action_minutes("combat_round")
    ctx.rule_refs.append(str(ref))
    if model.status != EncounterStatus.ACTIVE:
        _end_encounter(ctx, enc, model)
    advance_game_time(ctx, minutes * rounds, "combat")


def _end_encounter(ctx: ToolContext, enc: EncounterState, model: Encounter) -> None:
    for p in enc.participants:
        if p.kind == ParticipantKind.MONSTER and p.status == CombatantStatus.DEFEATED.value and p.entity_id:
            ev = ctx.event("CREATURE_DEFEATED", f"{p.name} was defeated", {"entity_id": str(p.entity_id)},
                           witnessed=True)
            ctx.session.add(DefeatedCreature(campaign_id=ctx.campaign.id, entity_id=p.entity_id, event_id=ev.id))
    if model.status == EncounterStatus.PLAYER_VICTORY:
        ctx.event("ENCOUNTER_ENDED", "Combat ended in victory", {"encounter_id": str(enc.id), "result": "victory"})
        ctx.outcome.append("The fight is over. You are victorious.")
    else:
        ctx.session.add(CharacterCondition(character_id=ctx.character.id, condition_key="incapacitated"))
        ctx.event("ENCOUNTER_ENDED", f"{ctx.character.name} was incapacitated",
                  {"encounter_id": str(enc.id), "result": "defeat"})
        ctx.outcome.append(f"{ctx.character.name} falls, incapacitated. What happens next is not defined by "
                           "the active rules profile.")


def wait(ctx: ToolContext, minutes: int) -> None:
    require_not_in_combat(ctx)
    advance_game_time(ctx, minutes, "waiting")
    ctx.outcome.append(f"You wait for {minutes} minutes.")


# ---- campaign start ------------------------------------------------------------


def start_campaign(ctx: ToolContext, start_location: AdventureEntity) -> None:
    session, campaign = ctx.session, ctx.campaign
    session.add(WorldClock(campaign_id=campaign.id, minutes_elapsed=0))
    session.flush()
    adventure_entities = session.scalars(select(AdventureEntity).where(
        AdventureEntity.adventure_id == campaign.adventure_id)).all()
    for q in (e for e in adventure_entities if e.entity_type == EntityType.QUEST):
        session.add(QuestState(campaign_id=campaign.id, quest_entity_id=q.id, status=QuestStatus.UNKNOWN))
    for npc in (e for e in adventure_entities if e.entity_type == EntityType.NPC):
        home = session.scalar(select(AdventureEdge.source_entity_id).where(
            AdventureEdge.target_entity_id == npc.id, AdventureEdge.edge_type == EdgeType.CONTAINS))
        definition = session.get(NPCDefinition, npc.id)
        inst = NPCInstance(campaign_id=campaign.id, npc_entity_id=npc.id, location_id=home,
                           disposition=definition.default_disposition, status=NpcStatus.ACTIVE)
        session.add(inst)
        session.flush()
        for edge in session.scalars(select(AdventureEdge).where(AdventureEdge.source_entity_id == npc.id,
                                                                AdventureEdge.edge_type == EdgeType.KNOWS)):
            target = session.get(AdventureEntity, edge.target_entity_id)
            if target:
                session.add(NPCMemory(npc_instance_id=inst.id, content=f"Knows about {target.name}: {target.summary}",
                                      learned_via=LearnedVia.SOURCE_DEFINED, passage_id=edge.evidence_passage_id,
                                      learned_at_minute=0))
    ctx.event("CAMPAIGN_STARTED", f"The adventure begins at {start_location.name}",
              {"start_location_id": str(start_location.id)})
    enter_location(ctx, start_location.id)


def clear_campaign_events_guard(session: Session) -> None:
    """Allow GameEvent deletion inside this transaction only (used by campaign deletion)."""
    session.execute(text("SET LOCAL lanternkeeper.allow_event_purge = 'on'"))


__all__ = [
    "READ_TOOLS",
    "ToolContext",
    "ToolRejected",
    "advance_game_time",
    "clear_campaign_events_guard",
    "move_to_location",
    "record_npc_interaction",
    "request_ability_check",
    "resolve_attack_tool",
    "reveal_entity",
    "search_location",
    "start_campaign",
    "update_quest_progress",
    "use_item",
    "wait",
]
