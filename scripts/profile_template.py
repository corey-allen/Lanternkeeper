"""Write a blank private rules profile for the owner to fill from their own rulebook.

Every entry starts UNVERIFIED with no value, so the profile blocks every mechanic until each value
is entered with a page citation and a verification date. The file goes to private/profiles/
(gitignored); point LK_RULES_PROFILE_DIR there.

Usage: uv run python scripts/profile_template.py <key> "<name>" [--out private/profiles]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lk_rules import RuleEntry, RulesProfile, RuleStatus, validate_profile
from lk_rules.profile import RULE_TYPES

ROOT = Path(__file__).resolve().parents[1]


def type_name(t: object) -> str:
    return getattr(t, "__name__", None) or str(t).replace("lk_rules.profile.", "")


def build(key: str, name: str) -> RulesProfile:
    entries = {
        rule: RuleEntry(status=RuleStatus.UNVERIFIED, source="TODO: <book> p.<page>",
                        notes=f"expected value type: {type_name(t)}")
        for rule, t in RULE_TYPES.items()
    }
    return RulesProfile(key=key, name=name, version="0.1.0", system="shadowdark",
                        description="Owner-verified profile built from the owner's own rulebook. Private.",
                        requires_house_rule_acceptance=False, entries=entries)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("name")
    ap.add_argument("--out", type=Path, default=ROOT / "private" / "profiles")
    args = ap.parse_args()
    profile = build(args.key, args.name)
    problems = validate_profile(profile)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    path = args.out / f"{args.key}.profile.json"
    if path.exists():
        print(f"{path} already exists; not overwriting", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
