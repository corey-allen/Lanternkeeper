"""Loading campaign state and building audience-specific projections.

``player_view`` is the ONLY projection used by player-facing routes. It is built
from canonical tables and includes DM-only adventure content only after the
campaign has recorded a discovery of it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, object_session

from lk_rules import RulesProfile
from lk_rules.character import AbilityScores
from lk_rules import Ability, RulesError
from lk_rules.character import ability_modifier
from lk_rules.clock import CampaignTiming, LightPolicy, timing_options
from lk_rules.gear import Purse, coin_slots
from lk_rules.inventory import Inventory, ItemStack
from lk_rules.modifiers import Feature, Modifier, Scope

from ..errors import ApiError, not_found
from ..models import (
    ActiveLight,
    AdventureEdge,
    AdventureEntity,
    Campaign,
    CampaignDiscovery,
    CampaignLocationState,
    Character,
    CharacterCoin,
    CharacterCondition,
    CharacterFeature,
    CharacterInventory,
    CharacterModifier,
    ClueDefinition,
    DefeatedCreature,
    DiceRoll,
    EdgeType,
    EncounterState,
    EncounterStatus,
    EntityType,
    GameEvent,
    Location,
    LocationConnection,
    MonsterDefinition,
    NPCDefinition,
    NPCInstance,
    NpcStatus,
    QuestDefinition,
    QuestState,
    QuestStatus,
    RulesProfileVersion,
    SecretDefinition,
    User,
    Visibility,
    WorldClock,
)


def load_campaign(session: Session, user: User, campaign_id: uuid.UUID, *, lock: bool = False) -> Campaign:
    stmt = select(Campaign).where(Campaign.id == campaign_id, Campaign.owner_id == user.id)
    if lock:
        stmt = stmt.with_for_update()
    campaign = session.scalar(stmt)
    if campaign is None:
        raise not_found("Campaign")
    return campaign


_PROFILE_CACHE: dict[uuid.UUID, RulesProfile] = {}


def profile_for(campaign: Campaign) -> RulesProfile:
    """The exact rules profile version this campaign is pinned to (never the live file)."""
    if campaign.rules_profile_version_id is None:
        raise ApiError(409, "RULES_PROFILE_NOT_PINNED", "This campaign has no pinned rules profile version")
    cached = _PROFILE_CACHE.get(campaign.rules_profile_version_id)
    if cached is not None:
        return cached
    session = object_session(campaign)
    assert session is not None, "campaign must be attached to a session"
    version = session.get(RulesProfileVersion, campaign.rules_profile_version_id)
    profile = RulesProfile.model_validate(version.definition)
    _PROFILE_CACHE[campaign.rules_profile_version_id] = profile
    return profile


def character_of(session: Session, campaign: Campaign) -> Character | None:
    return session.scalar(select(Character).where(Character.campaign_id == campaign.id))


def require_character(session: Session, campaign: Campaign) -> Character:
    ch = character_of(session, campaign)
    if ch is None:
        raise ApiError(409, "NO_CHARACTER", "Create a character first")
    return ch


def scores(ch: Character) -> AbilityScores:
    return AbilityScores(STR=ch.str_score, DEX=ch.dex_score, CON=ch.con_score, INT=ch.int_score,
                         WIS=ch.wis_score, CHA=ch.cha_score)


def _coin_slots(session: Session, ch: Character, campaign: Campaign) -> int:
    try:
        return coin_slots(purse_of(session, ch), profile_for(campaign))
    except RulesError:
        return 0


def character_modifiers(session: Session, ch: Character) -> list[Modifier]:
    """Active typed modifiers (STAT_SCORE entries are already folded into scores and skipped)."""
    out = []
    for m in session.scalars(select(CharacterModifier).where(CharacterModifier.character_id == ch.id,
                                                             CharacterModifier.folded.is_(False))):
        out.append(Modifier(target=m.target, mode=m.mode, amount=m.amount, amount_from_stat=m.amount_from_stat,
                            positive_only=m.positive_only, per_level_divisor=m.per_level_divisor,
                            scope=Scope(**m.scope), source=m.source, label=m.label))
    return out


def character_features(session: Session, ch: Character) -> list[Feature]:
    return [Feature(key=f.key, name=f.name, kind=f.kind, uses_per=f.uses_per, uses=f.uses_max,
                    tags=tuple(f.tags), source=f.source)
            for f in session.scalars(select(CharacterFeature).where(CharacterFeature.character_id == ch.id))]


def inventory_rows(session: Session, ch: Character) -> list[CharacterInventory]:
    return list(session.scalars(select(CharacterInventory).where(CharacterInventory.character_id == ch.id)
                                .order_by(CharacterInventory.item_key)))


def purse_of(session: Session, ch: Character) -> Purse:
    rows = session.scalars(select(CharacterCoin).where(CharacterCoin.character_id == ch.id))
    return Purse(coins={r.denomination: r.count for r in rows if r.count})


def save_purse(session: Session, ch: Character, purse: Purse) -> None:
    existing = {r.denomination: r for r in session.scalars(select(CharacterCoin).where(CharacterCoin.character_id == ch.id))}
    for denom, row in existing.items():
        if denom not in purse.coins:
            session.delete(row)
    for denom, count in purse.coins.items():
        if denom in existing:
            existing[denom].count = count
        else:
            session.add(CharacterCoin(character_id=ch.id, denomination=denom, count=count))


def save_inventory(session: Session, ch: Character, inventory: Inventory) -> None:
    """Replace the character's item rows with ``inventory`` (keyed by item)."""
    rows = {r.item_key: r for r in inventory_rows(session, ch)}
    wanted = {s.item_key: s for s in inventory.stacks}
    for key, row in rows.items():
        if key not in wanted:
            session.delete(row)
    for key, s in wanted.items():
        row = rows.get(key) or CharacterInventory(character_id=ch.id, item_key=key)
        row.name, row.quantity, row.slots_each = s.name, s.quantity, s.slots_each
        row.quantity_per_slot, row.free_quantity = s.quantity_per_slot, s.free_quantity
        row.equipped_as, row.equipped = s.equipped_as, s.equipped_as is not None
        session.add(row)
    session.flush()


def stat_mods_of(ch: Character, profile: RulesProfile) -> dict[Ability, int]:
    sc = scores(ch)
    return {a: ability_modifier(sc.score(a), profile)[0] for a in Ability}


def inventory_model(rows: list[CharacterInventory]) -> Inventory:
    return Inventory(stacks=tuple(ItemStack(item_key=r.item_key, name=r.name, quantity=r.quantity,
                                            slots_each=r.slots_each, quantity_per_slot=r.quantity_per_slot,
                                            free_quantity=r.free_quantity, equipped_as=r.equipped_as,
                                            equipped=r.equipped_as is not None) for r in rows))


def discovered_ids(session: Session, campaign_id: uuid.UUID) -> set[uuid.UUID]:
    return set(session.scalars(select(CampaignDiscovery.entity_id).where(CampaignDiscovery.campaign_id == campaign_id)))


def entity(session: Session, entity_id: uuid.UUID) -> AdventureEntity | None:
    return session.get(AdventureEntity, entity_id)


def exits(session: Session, campaign: Campaign, discovered: set[uuid.UUID]) -> list[dict[str, Any]]:
    here = campaign.current_location_id
    if here is None:
        return []
    conns = session.scalars(select(LocationConnection).where(or_(
        LocationConnection.from_location_id == here,
        (LocationConnection.to_location_id == here) & LocationConnection.bidirectional.is_(True)))).all()
    out = []
    for c in conns:
        dest = c.to_location_id if c.from_location_id == here else c.from_location_id
        if c.hidden and dest not in discovered:
            continue
        ent = session.get(AdventureEntity, dest)
        label = c.label if c.from_location_id == here else ""
        out.append({"location_id": str(dest), "name": ent.name if ent else "?", "label": label,
                    "connection_id": str(c.id)})
    return sorted(out, key=lambda x: x["name"])


def contained(session: Session, location_id: uuid.UUID, etype: EntityType) -> list[AdventureEntity]:
    return list(session.scalars(
        select(AdventureEntity).join(AdventureEdge, AdventureEdge.target_entity_id == AdventureEntity.id)
        .where(AdventureEdge.source_entity_id == location_id, AdventureEdge.edge_type == EdgeType.CONTAINS,
               AdventureEntity.entity_type == etype).order_by(AdventureEntity.source_order)))


def npcs_present(session: Session, campaign: Campaign) -> list[tuple[NPCInstance, AdventureEntity]]:
    if campaign.current_location_id is None:
        return []
    rows = session.execute(select(NPCInstance, AdventureEntity)
                           .join(AdventureEntity, AdventureEntity.id == NPCInstance.npc_entity_id)
                           .where(NPCInstance.campaign_id == campaign.id,
                                  NPCInstance.location_id == campaign.current_location_id,
                                  NPCInstance.status == NpcStatus.ACTIVE)
                           .order_by(AdventureEntity.source_order)).all()
    return [(i, e) for i, e in rows]


def enemies_present(session: Session, campaign: Campaign) -> list[tuple[AdventureEntity, MonsterDefinition]]:
    if campaign.current_location_id is None:
        return []
    defeated = set(session.scalars(select(DefeatedCreature.entity_id).where(DefeatedCreature.campaign_id == campaign.id)))
    out = []
    for e in contained(session, campaign.current_location_id, EntityType.MONSTER):
        if e.id not in defeated:
            out.append((e, session.get(MonsterDefinition, e.id)))
    return out


def active_encounter(session: Session, campaign: Campaign) -> EncounterState | None:
    return session.scalar(select(EncounterState).where(EncounterState.campaign_id == campaign.id,
                                                      EncounterState.status == EncounterStatus.ACTIVE))


def clock(session: Session, campaign: Campaign) -> tuple[int, list[ActiveLight]]:
    wc = session.get(WorldClock, campaign.id)
    lights = list(session.scalars(select(ActiveLight).where(ActiveLight.campaign_id == campaign.id)))
    return (wc.minutes_elapsed if wc else 0), lights


def is_lit(session: Session, campaign: Campaign) -> bool:
    return any(li.remaining_minutes > 0 for li in clock(session, campaign)[1])


def timing_of(campaign: Campaign, profile: RulesProfile) -> tuple[CampaignTiming, LightPolicy]:
    """The campaign's timing mode and multiple-light option; the profile's defaults when unset."""
    opts = timing_options(profile)
    mode = campaign.light_timing if campaign.light_timing in opts.modes else opts.modes[0]
    policy = campaign.light_multiple if campaign.light_multiple in opts.multiple_sources else opts.multiple_sources[0]
    return mode, policy  # type: ignore[return-value]


def clock_view(session: Session, campaign: Campaign) -> dict[str, Any]:
    """Player-facing clock. Light seconds subtract real time credited but not yet flushed (ADR-0013)."""
    wc = session.get(WorldClock, campaign.id)
    minutes, lights = clock(session, campaign)
    pending = wc.realtime_pending_seconds if wc else 0
    try:
        profile = profile_for(campaign)
        opts = timing_options(profile)
        mode, policy = timing_of(campaign, profile)
    except (RulesError, ApiError):
        opts, mode, policy = None, None, None
    return {
        "minutes_elapsed": minutes, "pending_seconds": pending,
        "running": bool(wc and wc.realtime_last_tick_at is not None),
        "timing": mode, "multiple_lights": policy, "idle_pause_seconds": campaign.idle_pause_seconds,
        "timing_options": list(opts.modes) if opts else [],
        "multiple_light_options": list(opts.multiple_sources) if opts else [],
        "lights": [{"item_key": li.item_key, "remaining_minutes": li.remaining_minutes,
                    "remaining_seconds": max(0, li.remaining_minutes * 60 - pending)} for li in lights],
        "is_lit": any(li.remaining_minutes * 60 - pending > 0 for li in lights),
    }


def restricted_texts(session: Session, campaign: Campaign, discovered: set[uuid.UUID]) -> dict[str, str]:
    """DM-only adventure text the player has not discovered. Used by the output guard."""
    if campaign.adventure_id is None:
        return {}
    rows = session.scalars(select(AdventureEntity).where(AdventureEntity.adventure_id == campaign.adventure_id,
                                                         AdventureEntity.visibility == Visibility.DM_ONLY)).all()
    out: dict[str, str] = {}
    for e in rows:
        if e.id in discovered:
            continue
        detail = None
        if e.entity_type == EntityType.CLUE:
            detail = session.get(ClueDefinition, e.id).content
        elif e.entity_type == EntityType.SECRET:
            detail = session.get(SecretDefinition, e.id).content
        elif e.entity_type == EntityType.QUEST:
            detail = session.get(QuestDefinition, e.id).objective
        out[str(e.id)] = detail or e.summary
    return out


def player_view(session: Session, campaign: Campaign) -> dict[str, Any]:
    discovered = discovered_ids(session, campaign.id)
    ch = character_of(session, campaign)
    view: dict[str, Any] = {
        "campaign": {"id": str(campaign.id), "name": campaign.name, "status": campaign.status.value,
                     "version": campaign.version, "rules_profile": campaign.rules_profile_id,
                     "adventure_id": str(campaign.adventure_id) if campaign.adventure_id else None},
        "character": None, "inventory": [], "conditions": [], "location": None, "exits": [], "npcs": [],
        "enemies": [], "quests": [], "discoveries": [],
        "clock": clock_view(session, campaign),
        "encounter": None, "recent_events": [], "recent_dice": [],
    }
    if ch:
        inv = inventory_rows(session, ch)
        view["character"] = {
            "id": str(ch.id), "name": ch.name, "class_key": ch.class_key, "level": ch.level,
            "ability_scores": scores(ch).model_dump(), "hp_current": ch.hp_current, "hp_max": ch.hp_max,
            "armor_class": ch.armor_class, "rule_refs": ch.rule_refs,
            "ancestry_key": ch.ancestry_key, "alignment": ch.alignment, "deity": ch.deity_key,
            "background": ch.background_key, "title": ch.title, "xp": ch.xp, "gear_slots": ch.gear_slots,
            "languages": ch.languages, "coins": purse_of(session, ch).coins, "pending": ch.pending,
            "gear_slots_used": inventory_model(inventory_rows(session, ch)).slots_used + _coin_slots(session, ch, campaign),
            "features": [{"key": f.key, "name": f.name, "kind": f.kind, "uses_per": f.uses_per,
                          "uses_remaining": f.uses_remaining}
                         for f in session.scalars(select(CharacterFeature).where(CharacterFeature.character_id == ch.id))],
            "modifiers": [{"target": m.target, "mode": m.mode, "amount": m.amount, "from_stat": m.amount_from_stat,
                           "scope": m.scope, "source": m.source, "label": m.label, "folded": m.folded}
                          for m in session.scalars(select(CharacterModifier).where(CharacterModifier.character_id == ch.id))],
        }
        view["inventory"] = [{"item_key": r.item_key, "name": r.name, "quantity": r.quantity,
                              "slots_each": r.slots_each, "quantity_per_slot": r.quantity_per_slot,
                              "free_quantity": r.free_quantity, "equipped_as": r.equipped_as} for r in inv]
        view["conditions"] = [{"key": c.condition_key, "expires_at_minute": c.expires_at_minute}
                              for c in session.scalars(select(CharacterCondition)
                                                       .where(CharacterCondition.character_id == ch.id))]
    if campaign.current_location_id:
        loc_e = session.get(AdventureEntity, campaign.current_location_id)
        loc = session.get(Location, campaign.current_location_id)
        view["location"] = {"id": str(loc_e.id), "name": loc_e.name, "description": loc.description if loc else "",
                            "citations": [str(ev.passage_id) for ev in loc_e.evidence]}
        view["exits"] = exits(session, campaign, discovered)
        view["npcs"] = [{"id": str(e.id), "name": e.name, "disposition": i.disposition.value,
                         "description": (session.get(NPCDefinition, e.id).description)}
                        for i, e in npcs_present(session, campaign)]
        view["enemies"] = [{"id": str(e.id), "name": e.name, "description": m.description}
                           for e, m in enemies_present(session, campaign)]
    quests = session.execute(select(QuestState, AdventureEntity)
                             .join(AdventureEntity, AdventureEntity.id == QuestState.quest_entity_id)
                             .where(QuestState.campaign_id == campaign.id,
                                    QuestState.status != QuestStatus.UNKNOWN)).all()
    view["quests"] = [{"id": str(e.id), "name": e.name, "status": q.status.value,
                       "objective": session.get(QuestDefinition, e.id).objective} for q, e in quests]
    for ent_id in discovered:
        e = session.get(AdventureEntity, ent_id)
        if e is None or e.entity_type not in (EntityType.CLUE, EntityType.SECRET):
            continue
        text = (session.get(ClueDefinition, e.id).content if e.entity_type == EntityType.CLUE
                else session.get(SecretDefinition, e.id).content)
        view["discoveries"].append({"id": str(e.id), "type": e.entity_type.value, "name": e.name, "text": text})
    view["discoveries"].sort(key=lambda d: d["name"])
    enc = active_encounter(session, campaign)
    if enc:
        view["encounter"] = {
            "round": enc.round, "turn_index": enc.turn_index,
            "participants": [{"name": p.name, "side": p.side, "status": p.status, "initiative": p.initiative,
                              **({"hp": p.hp, "max_hp": p.max_hp} if p.side == "player" else {})}
                             for p in enc.participants]}
    events = session.scalars(select(GameEvent).where(GameEvent.campaign_id == campaign.id,
                                                     GameEvent.visibility == Visibility.PLAYER_VISIBLE)
                             .order_by(GameEvent.sequence.desc()).limit(20)).all()
    view["recent_events"] = [event_dict(ev) for ev in events]
    rolls = session.scalars(select(DiceRoll).where(DiceRoll.campaign_id == campaign.id)
                            .order_by(DiceRoll.created_at.desc()).limit(10)).all()
    view["recent_dice"] = [roll_dict(r) for r in rolls]
    return view


def event_dict(ev: GameEvent, *, include_payload: bool = False) -> dict[str, Any]:
    out = {"sequence": ev.sequence, "type": ev.event_type, "summary": ev.summary, "game_minute": ev.game_minute,
           "campaign_version": ev.campaign_version, "visibility": ev.visibility.value,
           "created_at": ev.created_at.isoformat()}
    if include_payload:
        out["payload"] = ev.payload
    return out


def roll_dict(r: DiceRoll) -> dict[str, Any]:
    return {"id": str(r.id), "actor": r.actor, "purpose": r.purpose, "expression": r.expression, "dice": r.dice,
            "modifier": r.modifier, "total": r.total, "rule_refs": r.rule_refs, "roller": r.roller_kind}


def canonical_state(session: Session, campaign: Campaign) -> dict[str, Any]:
    """Complete canonical campaign state (no source text), used for snapshots, export, and recovery checks."""
    ch = character_of(session, campaign)
    minutes, lights = clock(session, campaign)
    state: dict[str, Any] = {
        "campaign": {"id": str(campaign.id), "version": campaign.version, "status": campaign.status.value,
                     "rules_profile": campaign.rules_profile_id,
                     "adventure_id": str(campaign.adventure_id) if campaign.adventure_id else None,
                     "current_location_id": str(campaign.current_location_id) if campaign.current_location_id else None},
        "clock": {"minutes": minutes, "lights": sorted([[li.item_key, li.remaining_minutes] for li in lights])},
        "character": None,
        "inventory": [], "conditions": [],
        "locations": sorted([[str(s.location_id), s.visited, s.searched] for s in session.scalars(
            select(CampaignLocationState).where(CampaignLocationState.campaign_id == campaign.id))]),
        "discoveries": sorted(str(i) for i in discovered_ids(session, campaign.id)),
        "quests": sorted([[str(q.quest_entity_id), q.status.value] for q in session.scalars(
            select(QuestState).where(QuestState.campaign_id == campaign.id))]),
        "npcs": sorted([[str(n.npc_entity_id), n.disposition.value, str(n.location_id), n.status.value]
                        for n in session.scalars(select(NPCInstance).where(NPCInstance.campaign_id == campaign.id))]),
        "defeated": sorted(str(d) for d in session.scalars(
            select(DefeatedCreature.entity_id).where(DefeatedCreature.campaign_id == campaign.id))),
        "event_count": len(session.scalars(select(GameEvent.id).where(GameEvent.campaign_id == campaign.id)).all()),
    }
    if ch:
        state["character"] = {"name": ch.name, "class_key": ch.class_key, "level": ch.level,
                              "scores": scores(ch).model_dump(), "hp": [ch.hp_current, ch.hp_max], "ac": ch.armor_class}
        state["inventory"] = [[r.item_key, r.quantity] for r in inventory_rows(session, ch)]
        state["conditions"] = sorted(c.condition_key for c in session.scalars(
            select(CharacterCondition).where(CharacterCondition.character_id == ch.id)))
    enc = active_encounter(session, campaign)
    state["encounter"] = None if enc is None else {
        "round": enc.round, "turn": enc.turn_index,
        "participants": [[p.name, p.hp, p.status] for p in enc.participants]}
    return state


def state_hash(state: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
