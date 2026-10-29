"""Dice expressions, rollers, and auditable roll records.

Production code must use :class:`SecureRoller`. :class:`SeededRoller` exists only
for automated tests; the API refuses to start with it outside the test environment.
"""

from __future__ import annotations

import random
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .errors import InvalidDiceExpression

MAX_DICE = 100
MAX_SIDES = 1000
_ALLOWED_SIDES = {2, 3, 4, 6, 8, 10, 12, 20, 100}

_TERM = re.compile(r"([+-]?)\s*(?:(\d*)d(\d+)|(\d+))", re.IGNORECASE)


class DiceTerm(BaseModel):
    model_config = ConfigDict(frozen=True)
    sign: Literal[1, -1] = 1
    count: int = Field(ge=1, le=MAX_DICE)
    sides: int = Field(ge=2, le=MAX_SIDES)


class DiceExpression(BaseModel):
    """A sum of dice terms and a flat modifier, e.g. ``2d6+1`` or ``d20-1``."""

    model_config = ConfigDict(frozen=True)
    terms: tuple[DiceTerm, ...]
    modifier: int = 0
    source: str

    @classmethod
    def parse(cls, text: str) -> DiceExpression:
        raw = text.strip().replace(" ", "")
        if not raw or len(raw) > 64:
            raise InvalidDiceExpression(f"Invalid dice expression: {text!r}")
        pos = 0
        terms: list[DiceTerm] = []
        modifier = 0
        for match in _TERM.finditer(raw):
            if match.start() != pos:
                raise InvalidDiceExpression(f"Invalid dice expression: {text!r}")
            if pos != 0 and not match.group(1):
                raise InvalidDiceExpression(f"Missing operator in dice expression: {text!r}")
            pos = match.end()
            sign = -1 if match.group(1) == "-" else 1
            if match.group(3):
                count = int(match.group(2) or 1)
                sides = int(match.group(3))
                if sides not in _ALLOWED_SIDES:
                    raise InvalidDiceExpression(f"Unsupported die size d{sides} in {text!r}")
                if count < 1 or count > MAX_DICE:
                    raise InvalidDiceExpression(f"Dice count out of range in {text!r}")
                terms.append(DiceTerm(sign=sign, count=count, sides=sides))
            else:
                modifier += sign * int(match.group(4))
        if pos != len(raw) or not terms:
            raise InvalidDiceExpression(f"Invalid dice expression: {text!r}")
        return cls(terms=tuple(terms), modifier=modifier, source=raw.lower())

    def max_total(self) -> int:
        return sum(t.sign * t.count * t.sides for t in self.terms) + self.modifier


class Roller(Protocol):
    kind: str

    def roll_die(self, sides: int) -> int: ...


class SecureRoller:
    """Cryptographically strong roller for production play."""

    kind = "secure"

    def __init__(self) -> None:
        self._rng = secrets.SystemRandom()

    def roll_die(self, sides: int) -> int:
        return self._rng.randint(1, sides)


class SeededRoller:
    """Deterministic roller. Tests only."""

    kind = "seeded-test-only"

    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def roll_die(self, sides: int) -> int:
        return self._rng.randint(1, sides)


class FixedRoller:
    """Returns a scripted sequence of die faces. Tests only."""

    kind = "fixed-test-only"

    def __init__(self, faces: list[int]) -> None:
        self._faces = list(faces)

    def roll_die(self, sides: int) -> int:
        if not self._faces:
            raise RuntimeError("FixedRoller exhausted")
        face = self._faces.pop(0)
        if not 1 <= face <= sides:
            raise ValueError(f"Scripted face {face} impossible on d{sides}")
        return face


class DieResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    sides: int
    value: int
    kept: bool = True


class RollRecord(BaseModel):
    """An auditable record of one resolved roll."""

    model_config = ConfigDict(frozen=True)
    roll_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    expression: str
    purpose: str
    actor: str
    dice: tuple[DieResult, ...]
    modifier: int
    total: int
    rule_refs: tuple[str, ...] = ()
    roller_kind: str
    rolled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def natural(self) -> int:
        """Sum of kept dice before modifiers."""
        return sum(d.value for d in self.dice if d.kept)


def roll(
    expression: DiceExpression | str,
    roller: Roller,
    *,
    purpose: str,
    actor: str,
    extra_modifier: int = 0,
    rule_refs: tuple[str, ...] = (),
) -> RollRecord:
    expr = DiceExpression.parse(expression) if isinstance(expression, str) else expression
    dice: list[DieResult] = []
    subtotal = 0
    for term in expr.terms:
        for _ in range(term.count):
            value = roller.roll_die(term.sides)
            dice.append(DieResult(sides=term.sides, value=value))
            subtotal += term.sign * value
    modifier = expr.modifier + extra_modifier
    return RollRecord(
        expression=expr.source,
        purpose=purpose,
        actor=actor,
        dice=tuple(dice),
        modifier=modifier,
        total=subtotal + modifier,
        rule_refs=rule_refs,
        roller_kind=roller.kind,
    )


def roll_die_with_mode(
    roller: Roller,
    *,
    sides: int,
    mode: Literal["normal", "advantage", "disadvantage"],
    modifier: int,
    purpose: str,
    actor: str,
    rule_refs: tuple[str, ...] = (),
) -> RollRecord:
    """Roll one die or, for advantage/disadvantage, two of the same die and keep one.

    Whether advantage applies is decided by the caller; this only implements the arithmetic.
    """
    first = roller.roll_die(sides)
    if mode == "normal":
        dice = (DieResult(sides=sides, value=first),)
        kept = first
    else:
        second = roller.roll_die(sides)
        keep_first = first >= second if mode == "advantage" else first <= second
        dice = (
            DieResult(sides=sides, value=first, kept=keep_first),
            DieResult(sides=sides, value=second, kept=not keep_first),
        )
        kept = first if keep_first else second
    expr = f"d{sides}" if mode == "normal" else (f"2d{sides}kh1" if mode == "advantage" else f"2d{sides}kl1")
    return RollRecord(
        expression=expr,
        purpose=purpose,
        actor=actor,
        dice=dice,
        modifier=modifier,
        total=kept + modifier,
        rule_refs=rule_refs,
        roller_kind=roller.kind,
    )


def roll_d20_with_mode(
    roller: Roller,
    *,
    mode: Literal["normal", "advantage", "disadvantage"],
    modifier: int,
    purpose: str,
    actor: str,
    rule_refs: tuple[str, ...] = (),
) -> RollRecord:
    return roll_die_with_mode(roller, sides=20, mode=mode, modifier=modifier, purpose=purpose, actor=actor,
                              rule_refs=rule_refs)
