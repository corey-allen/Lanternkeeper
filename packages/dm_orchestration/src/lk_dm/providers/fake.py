"""Deterministic fake provider for tests and offline development.

It interprets player input with simple keyword rules against the SCENE OPTIONS
section and narrates by restating the committed outcome. It can also be given a
script of canned responses or exceptions to simulate misbehaving models.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..context import SECTION_FACTS, SECTION_INPUT, SECTION_OPTIONS, SECTION_OUTCOME, extract_section
from ..schemas import IntentKind, IntentProposal, SceneOptions
from .base import Message, ProviderResponseInvalid

T = TypeVar("T", bound=BaseModel)

_UNSUPPORTED = [
    (r"\b(rest|sleep|camp)\b", "rest.recover"),
    (r"\b(cast|spell|incantation)\b", "spell.cast"),
    (r"\blevel ?up\b|\bexperience points?\b", "advancement.level"),
    (r"\b(persuade|convince|intimidate|bribe)\b", "social.persuade"),
]
_CHECK_VERBS = {
    "STR": r"\b(climb|force|lift|push|break|jump)\b",
    "DEX": r"\b(sneak|balance|dodge|pick the lock|tumble)\b",
    "INT": r"\b(recall|decipher|study)\b",
    "WIS": r"\b(listen|notice|sense)\b",
}


def _mentions(text: str, name: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9']+", name.lower()) if w not in {"the", "a", "an", "of"}]
    return bool(words) and any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def interpret(options: SceneOptions, player_input: str) -> IntentProposal:
    text = player_input.lower()
    for pattern, mech in _UNSUPPORTED:
        if re.search(pattern, text):
            return IntentProposal(intent=IntentKind.UNSUPPORTED, unsupported_mechanic=mech)
    if re.search(r"\b(attack|strike|stab|hit|fight|swing at)\b", text):
        target = next((e for e in options.enemies if _mentions(text, e.name)), None)
        if target is None and len(options.enemies) == 1:
            target = options.enemies[0]
        if target:
            return IntentProposal(intent=IntentKind.ATTACK, target_id=target.id)
        return IntentProposal(intent=IntentKind.CLARIFY, clarification="Attack whom?")
    if re.search(r"\b(talk|speak|ask|greet|say)\b", text):
        npc = next((n for n in options.npcs if _mentions(text, n.name)), None)
        if npc:
            return IntentProposal(intent=IntentKind.TALK, target_id=npc.id, topic=player_input[:300])
    if re.search(r"\b(light|use|ignite)\b", text):
        item = next((i for i in options.items if _mentions(text, i.name)), None)
        if item:
            return IntentProposal(intent=IntentKind.USE_ITEM, item_key=item.id)
    if re.search(r"\b(go|move|walk|head|enter|travel|proceed|climb up|descend)\b", text):
        dest = next((x for x in options.exits if _mentions(text, x.name)), None)
        if dest:
            return IntentProposal(intent=IntentKind.MOVE, target_id=dest.id)
    if re.search(r"\b(search|investigate|examine|inspect|look for)\b", text):
        return IntentProposal(intent=IntentKind.SEARCH)
    for ability, pattern in _CHECK_VERBS.items():
        if re.search(pattern, text):
            return IntentProposal(intent=IntentKind.ABILITY_CHECK, ability=ability, difficulty="normal")
    if "?" in text or re.match(r"\s*(what|who|where|why|how|describe|look|tell me)\b", text):
        return IntentProposal(intent=IntentKind.ASK, topic=player_input[:300])
    if re.search(r"\bwait\b", text):
        return IntentProposal(intent=IntentKind.WAIT, wait_minutes=10)
    return IntentProposal(intent=IntentKind.CLARIFY, clarification="What would you like to do?")


class FakeProvider:
    name = "fake"
    is_remote = False

    def __init__(self, script: list[Any] | None = None) -> None:
        self.script: list[Any] = list(script or [])
        self.calls: list[list[Message]] = []

    def _next_scripted(self) -> Any:
        if not self.script:
            return None
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def generate_structured(
        self, messages: list[Message], response_schema: type[T], *, model: str, timeout_seconds: float
    ) -> T:
        self.calls.append(messages)
        scripted = self._next_scripted()
        if scripted is not None:
            try:
                if isinstance(scripted, str):
                    return response_schema.model_validate_json(scripted)
                if isinstance(scripted, dict):
                    return response_schema.model_validate(scripted)
                return response_schema.model_validate(scripted.model_dump())
            except ValidationError as exc:
                raise ProviderResponseInvalid(f"invalid structured response: {exc.error_count()} errors") from exc
        if response_schema is not IntentProposal:
            raise ProviderResponseInvalid(f"fake provider cannot produce {response_schema.__name__}")
        content = messages[-1].content
        options = SceneOptions.model_validate(json.loads(extract_section(content, SECTION_OPTIONS)))
        return interpret(options, extract_section(content, SECTION_INPUT))  # type: ignore[return-value]

    async def generate_text(self, messages: list[Message], *, model: str, timeout_seconds: float) -> str:
        self.calls.append(messages)
        scripted = self._next_scripted()
        if scripted is not None:
            return str(scripted)
        content = messages[-1].content
        outcome = extract_section(content, SECTION_OUTCOME)
        facts = extract_section(content, SECTION_FACTS)
        lines = [line[2:] for line in outcome.splitlines() if line.startswith("- ")]
        text = " ".join(lines)
        if facts and facts != "(none)":
            first = facts.split("\n\n")[0]
            body = first.split("\n", 1)[1] if "\n" in first else first
            text += f" {body.strip()}"
        return text.strip()
