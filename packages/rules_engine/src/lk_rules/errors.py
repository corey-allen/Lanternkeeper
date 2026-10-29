"""Typed errors raised by the rules engine.

Every failure is explicit. Callers must never fall back to model-generated
rules when one of these is raised.
"""

from __future__ import annotations


class RulesError(Exception):
    """Base class for all rules-engine errors."""

    code = "RULES_ERROR"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "details": self.details}


class UnsupportedMechanic(RulesError):
    """The mechanic is not implemented by the engine at all."""

    code = "UNSUPPORTED_MECHANIC"

    def __init__(self, mechanic: str, reason: str = "") -> None:
        super().__init__(
            f"Mechanic '{mechanic}' is not supported. {reason}".strip(),
            mechanic=mechanic,
        )
        self.mechanic = mechanic


class UnverifiedRule(RulesError):
    """The mechanic is implemented, but the active profile has no verified value for it."""

    code = "UNVERIFIED_RULE"

    def __init__(self, rule_key: str, profile_key: str, status: str) -> None:
        super().__init__(
            f"Rule '{rule_key}' in profile '{profile_key}' has status {status}; "
            "it must be verified or adopted as an explicit house rule before use.",
            rule_key=rule_key,
            profile_key=profile_key,
            status=status,
        )
        self.rule_key = rule_key


class InvalidDiceExpression(RulesError):
    code = "INVALID_DICE_EXPRESSION"


class ValidationFailed(RulesError):
    """Input failed rules validation; ``issues`` lists every problem found."""

    code = "VALIDATION_FAILED"

    def __init__(self, issues: list[str]) -> None:
        super().__init__("; ".join(issues), issues=issues)
        self.issues = issues


class InventoryError(RulesError):
    code = "INVENTORY_ERROR"


class CombatError(RulesError):
    code = "COMBAT_ERROR"


class InvalidTransition(RulesError):
    code = "INVALID_TRANSITION"
