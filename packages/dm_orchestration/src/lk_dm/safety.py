"""Defence-in-depth checks for untrusted text and model output."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

_INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|rules|prompts)",
    r"disregard (the |all )?(previous|prior|above|system)",
    r"\b(system|developer) (prompt|message|instruction)s?\b",
    r"\byou are (now )?(an? )?(ai|assistant|language model|chatgpt|claude)\b",
    r"\b(note|message|instructions?) (to|for) (the )?(ai|assistant|model|llm|dm bot)\b",
    r"\breveal (every|all) (secret|secrets)\b",
    r"\b(award|give|grant) the player\b",
    r"<\s*/?\s*(system|assistant|instructions?)\s*>",
]
_INJECTION = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def detect_prompt_injection(text: str) -> list[str]:
    """Return matched phrases. Detection only flags; it is not the protection.

    The protection is structural: source text is never treated as instructions and
    cannot trigger tools. Flags let reviewers see suspicious passages.
    """
    return [m.group(0) for m in _INJECTION.finditer(text)]


_WORD = re.compile(r"[a-z0-9']+")


def _ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    words = _WORD.findall(text.lower())
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


class GuardResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    text: str
    violations: tuple[str, ...]


def guard_output(text: str, restricted: dict[str, str], *, n: int = 5) -> GuardResult:
    """Withhold model output that reproduces restricted (DM-only) text.

    ``restricted`` maps an id to its protected text. If the output shares any
    n-word sequence with a restricted text, the whole output is replaced. A
    partial redaction could still leak meaning, so this fails closed.
    """
    out_grams = _ngrams(text, n)
    violations = tuple(
        key for key, secret in restricted.items() if out_grams & _ngrams(secret, n)
    )
    if violations:
        return GuardResult(
            text="[Narration withheld: it referred to information your character has not discovered.]",
            violations=violations,
        )
    return GuardResult(text=text, violations=())
