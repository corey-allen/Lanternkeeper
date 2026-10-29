"""Review a private rules profile entry by entry and mark entries verified.

Verification is the owner's act: compare each value with the cited page of your own book, then
verify it. Only VERIFIED (or explicit HOUSE_RULE) entries are usable in play.

  uv run python scripts/review_profile.py list   private/profiles/shadowdark-core.profile.json
  uv run python scripts/review_profile.py show   <file> check.difficulty
  uv run python scripts/review_profile.py verify <file> check.difficulty [more keys...]
  uv run python scripts/review_profile.py verify <file> --all-transcribed
  uv run python scripts/review_profile.py unverify <file> check.difficulty

Restart the API afterwards. Existing campaigns stay on their pinned version until upgraded.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from lk_rules import RulesProfile, RuleStatus, validate_profile


def load(path: Path) -> RulesProfile:
    return RulesProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save(path: Path, profile: RulesProfile) -> None:
    problems = validate_profile(profile)
    if problems:
        print("Not saved; the profile would be invalid:\n  " + "\n  ".join(problems), file=sys.stderr)
        raise SystemExit(1)
    path.write_text(json.dumps(profile.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["list", "show", "verify", "unverify"])
    ap.add_argument("file", type=Path)
    ap.add_argument("keys", nargs="*")
    ap.add_argument("--all-transcribed", action="store_true",
                    help="verify every entry that has a value (only after checking them all)")
    args = ap.parse_args()
    profile = load(args.file)

    if args.command == "list":
        for key, e in profile.entries.items():
            flag = "value" if e.value is not None else "empty"
            print(f"{e.status.value:<11} {flag:<6} {key:<28} {e.source}")
        return 0
    if args.command == "show":
        for key in args.keys:
            e = profile.entries[key]
            print(f"== {key}  [{e.status.value}]  {e.source}\n{e.notes}\n{json.dumps(e.value, indent=2)}")
        return 0

    keys = list(args.keys)
    if args.command == "verify" and args.all_transcribed:
        keys = [k for k, e in profile.entries.items() if e.value is not None and e.status == RuleStatus.UNVERIFIED]
    missing = [k for k in keys if k not in profile.entries]
    if missing or not keys:
        print(f"Unknown or no keys: {missing}", file=sys.stderr)
        return 1
    entries = dict(profile.entries)
    for key in keys:
        if args.command == "verify":
            entries[key] = entries[key].model_copy(update={"status": RuleStatus.VERIFIED, "verified_on": date.today()})
        else:
            entries[key] = entries[key].model_copy(update={"status": RuleStatus.UNVERIFIED, "verified_on": None})
    save(args.file, profile.model_copy(update={"entries": entries}))
    print(f"{args.command}: {', '.join(keys)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
