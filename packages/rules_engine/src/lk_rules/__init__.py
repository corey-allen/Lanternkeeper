"""Lanternkeeper deterministic rules engine.

Depends only on the standard library and pydantic. No web framework, database,
or model provider imports are allowed in this package (enforced by a test).
"""

from .builtin_profiles import BUILTIN_PROFILES, DEV_HOUSE_RULES, SHADOWDARK_UNVERIFIED, get_profile
from .errors import (
    CombatError,
    InvalidDiceExpression,
    InvalidTransition,
    InventoryError,
    RulesError,
    UnsupportedMechanic,
    UnverifiedRule,
    ValidationFailed,
)
from .profile import Ability, RuleEntry, RulesProfile, RuleStatus, validate_profile

__all__ = [
    "BUILTIN_PROFILES",
    "DEV_HOUSE_RULES",
    "SHADOWDARK_UNVERIFIED",
    "Ability",
    "CombatError",
    "InvalidDiceExpression",
    "InvalidTransition",
    "InventoryError",
    "RuleEntry",
    "RuleStatus",
    "RulesError",
    "RulesProfile",
    "UnsupportedMechanic",
    "UnverifiedRule",
    "ValidationFailed",
    "get_profile",
    "validate_profile",
]
