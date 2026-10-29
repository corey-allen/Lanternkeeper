"""Rules profile registry: built-in profiles plus the owner's private profile files.

Private profiles are JSON files (``*.profile.json``) in ``LK_RULES_PROFILE_DIR``. They are
validated strictly at startup; an invalid file stops the app rather than being skipped,
because silently missing rules would change play.

Campaigns never read the registry directly during play. Each campaign is pinned to an exact
profile version (``rules_profile_version``), so editing a profile file cannot change an ongoing
campaign until the owner explicitly upgrades it.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from lk_rules import BUILTIN_PROFILES, RulesProfile, validate_profile


class ProfileLoadError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("Invalid rules profiles: " + "; ".join(problems))
        self.problems = problems


def load_profile_dir(directory: str | Path | None) -> dict[str, RulesProfile]:
    if not directory:
        return {}
    root = Path(directory)
    if not root.is_dir():
        raise ProfileLoadError([f"LK_RULES_PROFILE_DIR {root} is not a directory"])
    loaded: dict[str, RulesProfile] = {}
    problems: list[str] = []
    for path in sorted(root.glob("*.profile.json")):
        try:
            profile = RulesProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, ValidationError) as exc:
            problems.append(f"{path.name}: not a valid profile ({type(exc).__name__})")
            continue
        issues = validate_profile(profile)
        problems.extend(f"{path.name}: {issue}" for issue in issues)
        if profile.key in BUILTIN_PROFILES:
            problems.append(f"{path.name}: key {profile.key!r} collides with a built-in profile")
        if profile.key in loaded:
            problems.append(f"{path.name}: duplicate key {profile.key!r}")
        if not issues:
            loaded[profile.key] = profile
    if problems:
        raise ProfileLoadError(problems)
    return loaded


class ProfileRegistry:
    def __init__(self, extra: dict[str, RulesProfile] | None = None) -> None:
        self.profiles: dict[str, RulesProfile] = {**BUILTIN_PROFILES, **(extra or {})}

    @classmethod
    def from_dir(cls, directory: str | Path | None) -> ProfileRegistry:
        return cls(load_profile_dir(directory))

    def get(self, key: str) -> RulesProfile:
        try:
            return self.profiles[key]
        except KeyError as exc:
            raise KeyError(f"Unknown rules profile {key!r}") from exc

    def is_private(self, key: str) -> bool:
        return key not in BUILTIN_PROFILES
