"""Fail if private or third-party source material is tracked by git.

Checks the git index for:
  * anything under private/
  * PDF, EPUB, or DOCX files outside fixtures/synthetic_adventures/
  * rules profiles exported from an owner's rulebook (*.profile.json outside fixtures/)

Usage: uv run python scripts/check_private_content.py   (exit 1 on any finding)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import PurePosixPath

ALLOWED_DOC_DIR = "fixtures/synthetic_adventures/"
DOC_SUFFIXES = {".pdf", ".epub", ".docx"}


def problems(paths: list[str]) -> list[str]:
    found = []
    for path in paths:
        p = PurePosixPath(path)
        if path.startswith("private/"):
            found.append(f"{path}: private/ must never be tracked")
        elif p.suffix.lower() in DOC_SUFFIXES and not path.startswith(ALLOWED_DOC_DIR):
            found.append(f"{path}: source documents may only be original fixtures in {ALLOWED_DOC_DIR}")
        elif path.endswith(".profile.json") and not path.startswith("fixtures/"):
            found.append(f"{path}: exported rules profiles stay in private/profiles/")
    return found


def main() -> int:
    tracked = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout.decode()
    found = problems([p for p in tracked.split("\0") if p])
    for line in found:
        print(f"PRIVATE CONTENT TRACKED: {line}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
