"""Graph validation over candidate drafts: duplicates, conflicts, broken references, evidence."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from ..models import CandidateType, WarningCode
from .extractor import CandidateDraft
from .text import normalize_key

_COMPARE_FIELDS = {
    CandidateType.NPC: ("disposition", "motivation"),
    CandidateType.MONSTER: ("armor_class", "hit_points", "attack_bonus", "damage"),
    CandidateType.CLUE: ("check_ability", "check_difficulty"),
}


@dataclass
class DraftWarning:
    code: WarningCode
    severity: str
    message: str
    draft_index: int | None = None


def similar(a: str, b: str) -> bool:
    return a == b or SequenceMatcher(None, a, b).ratio() >= 0.88


def validate_drafts(drafts: list[CandidateDraft]) -> tuple[list[DraftWarning], set[int]]:
    """Return warnings and the indices of drafts that should be marked UNCERTAIN."""
    warnings: list[DraftWarning] = []
    uncertain: set[int] = set()
    entities = [(i, d) for i, d in enumerate(drafts)
                if d.candidate_type not in (CandidateType.CONNECTION, CandidateType.EDGE)]

    for i, d in enumerate(drafts):
        if not d.evidence:
            warnings.append(DraftWarning(WarningCode.NO_EVIDENCE, "error",
                                         f"{d.candidate_type.value} '{d.name}' has no supporting passage", i))
            uncertain.add(i)
        for problem in d.problems:
            warnings.append(DraftWarning(WarningCode.CONFLICTING_FACT, "warning",
                                         f"{d.candidate_type.value} '{d.name}': {problem}", i))
            uncertain.add(i)

    for x, (i, a) in enumerate(entities):
        for j, b in entities[x + 1:]:
            if a.candidate_type != b.candidate_type or not similar(a.key, b.key):
                continue
            warnings.append(DraftWarning(
                WarningCode.DUPLICATE_ENTITY, "warning",
                f"{a.candidate_type.value} '{b.name}' looks like a duplicate of '{a.name}'", j))
            uncertain.update({i, j})
            for f in _COMPARE_FIELDS.get(a.candidate_type, ()):
                if a.data.get(f) and b.data.get(f) and a.data.get(f) != b.data.get(f):
                    warnings.append(DraftWarning(
                        WarningCode.CONFLICTING_FACT, "warning",
                        f"'{a.name}' and '{b.name}' disagree on {f}: {a.data.get(f)!r} vs {b.data.get(f)!r}", j))

    seen_pairs: dict[frozenset[str], int] = {}
    for i, d in enumerate(drafts):
        if d.candidate_type != CandidateType.CONNECTION:
            continue
        pair = frozenset({normalize_key(str(d.data.get("from_location", ""))),
                          normalize_key(str(d.data.get("to_location", "")))})
        if pair in seen_pairs:
            warnings.append(DraftWarning(
                WarningCode.DUPLICATE_ENTITY, "info",
                f"Connection '{d.name}' repeats '{drafts[seen_pairs[pair]].name}' in the other direction; "
                "approve one (connections are bidirectional by default)", i))
            uncertain.add(i)
        else:
            seen_pairs[pair] = i

    location_keys = [d.key for _, d in entities if d.candidate_type == CandidateType.LOCATION]
    entity_keys = [d.key for _, d in entities]
    for i, d in enumerate(drafts):
        if d.candidate_type == CandidateType.CONNECTION:
            for end in ("from_location", "to_location"):
                key = normalize_key(str(d.data.get(end, "")))
                if not any(similar(key, k) for k in location_keys):
                    warnings.append(DraftWarning(
                        WarningCode.BROKEN_REFERENCE, "error",
                        f"Connection '{d.name}' refers to unknown location '{d.data.get(end)}'", i))
                    uncertain.add(i)
        elif d.candidate_type == CandidateType.EDGE:
            for end in ("source", "target"):
                key = normalize_key(str(d.data.get(end, "")))
                if not any(similar(key, k) for k in entity_keys):
                    warnings.append(DraftWarning(
                        WarningCode.BROKEN_REFERENCE, "error",
                        f"Relationship '{d.name}' refers to unknown entity '{d.data.get(end)}'", i))
                    uncertain.add(i)
    return warnings, uncertain
