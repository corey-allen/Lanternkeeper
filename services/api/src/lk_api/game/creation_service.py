"""Persisting character creation: drafts, the finished character, its modifiers and features."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from lk_rules import RulesProfile
from lk_rules.creation import CharacterBuild, CreationDraft, finalize
from lk_rules.definitions import AncestryDef, ClassDef, CurrencyDef, DeityDef, ItemDef, LanguagesDef, NamedEntry
from lk_rules.dice import RollRecord
from lk_rules.errors import RulesError
from lk_rules.gear import Purse
from lk_rules.inventory import Inventory
from lk_rules.modifiers import ModTarget

from ..models import (
    Campaign,
    Character,
    CharacterDraft,
    CharacterFeature,
    CharacterModifier,
    DiceRoll,
)
from . import state as st
from . import tools


def record_rolls(s: Session, campaign: Campaign, rolls: list[RollRecord]) -> None:
    for rec in rolls:
        s.add(DiceRoll(id=uuid.UUID(rec.roll_id), campaign_id=campaign.id, action_id=None, event_id=None,
                       actor=rec.actor, purpose=rec.purpose, expression=rec.expression,
                       dice=[d.model_dump() for d in rec.dice], modifier=rec.modifier, total=rec.total,
                       rule_refs=list(rec.rule_refs), roller_kind=rec.roller_kind))


def load_draft(s: Session, campaign: Campaign, *, lock: bool = False) -> tuple[CharacterDraft | None, CreationDraft | None]:
    row = s.get(CharacterDraft, campaign.id, with_for_update=lock)
    return row, (CreationDraft.model_validate(row.state) if row else None)


def save_draft(s: Session, campaign: Campaign, draft: CreationDraft, row: CharacterDraft | None = None) -> None:
    if row is None:
        s.add(CharacterDraft(campaign_id=campaign.id, state=draft.model_dump(mode="json")))
    else:
        row.state = draft.model_dump(mode="json")
    s.flush()


def draft_options(profile: RulesProfile) -> dict[str, Any]:
    """Everything a creation UI needs to present choices. Values come from the pinned profile."""

    def opt(key, type_):
        try:
            value, _ = profile.optional(key, type_)
        except RulesError:
            return None
        return value

    ancestries = opt("character.ancestries", list[AncestryDef]) or []
    classes = opt("character.classes", list[ClassDef]) or []
    return {
        "ancestries": [a.model_dump(mode="json") for a in ancestries],
        "classes": [c.model_dump(mode="json") for c in classes],
        "alignments": opt("character.alignments", list[str]) or [],
        "deities": [d.model_dump(mode="json") for d in opt("character.deities", list[DeityDef]) or []],
        "backgrounds": [b.model_dump(mode="json") for b in opt("character.backgrounds", list[NamedEntry]) or []],
        "languages": (lambda v: v.model_dump(mode="json") if v else None)(opt("character.languages", LanguagesDef)),
        "items": [i.model_dump(mode="json") for i in opt("items.catalog", list[ItemDef]) or [] if i.cost],
        "currency": (lambda v: v.model_dump(mode="json") if v else None)(opt("items.currency", CurrencyDef)),
    }


def preview(draft: CreationDraft, profile: RulesProfile) -> dict[str, Any]:
    if draft.stage != "rolled":
        return {"ready": False, "issues": ["roll hit points and talents first"]}
    try:
        build = finalize(draft, profile)
    except RulesError as exc:
        return {"ready": False, "issues": exc.details.get("issues") or [exc.message]}
    return {"ready": True, "issues": [], "build": build.model_dump(mode="json")}


def persist_build(s: Session, campaign: Campaign, build: CharacterBuild, inventory: Inventory,
                  profile: RulesProfile, roller) -> Character:
    sc = build.scores
    ch = Character(campaign_id=campaign.id, name=build.name, class_key=build.class_key, level=build.level,
                   str_score=sc["STR"], dex_score=sc["DEX"], con_score=sc["CON"], int_score=sc["INT"],
                   wis_score=sc["WIS"], cha_score=sc["CHA"], hp_current=build.max_hp, hp_max=build.max_hp,
                   armor_class=build.armor_class, rule_refs=list(build.rule_refs), ancestry_key=build.ancestry_key,
                   alignment=build.alignment, deity_key=build.deity, background_key=build.background,
                   title=build.title, gear_slots=build.gear_slots, languages=list(build.languages),
                   base_scores=build.base_scores,
                   pending=list(build.pending))
    s.add(ch)
    s.flush()
    for m in build.modifiers:
        s.add(CharacterModifier(
            character_id=ch.id, target=m.target.value, mode=m.mode.value, amount=m.amount,
            amount_from_stat=m.amount_from_stat.value if m.amount_from_stat else None,
            positive_only=m.positive_only, per_level_divisor=m.per_level_divisor,
            scope=m.scope.model_dump(mode="json", exclude_none=True), source=m.source, label=m.label,
            folded=m.target == ModTarget.STAT_SCORE))
    for f in build.features:
        s.add(CharacterFeature(character_id=ch.id, key=f.key, name=f.name, kind=f.kind.value, uses_per=f.uses_per,
                               uses_max=f.uses, uses_remaining=f.uses, tags=list(f.tags), source=f.source))
    items = Inventory(stacks=tuple(inventory.stacks) + tuple(build.items))
    st.save_inventory(s, ch, items)
    st.save_purse(s, ch, Purse(coins=dict(build.coins)))
    ctx = tools.ToolContext(session=s, campaign=campaign, character=ch, profile=profile, roller=roller, action_id=None)
    who = f"{build.ancestry_name} {build.class_name}" if build.ancestry_name else build.class_name
    ctx.event("CHARACTER_CREATED", f"{ch.name} the {who} joined the campaign",
              {"name": ch.name, "class_key": ch.class_key, "ancestry_key": ch.ancestry_key, "max_hp": ch.hp_max,
               "base_scores": build.base_scores, "scores": build.scores, "pending": list(build.pending),
               "modifiers": [m.model_dump(mode="json") for m in build.modifiers], "rule_refs": list(build.rule_refs)})
    campaign.version += 1
    s.flush()
    return ch
