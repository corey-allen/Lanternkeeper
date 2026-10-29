"""The repository must never track owner-supplied rulebooks, adventures, or private profiles."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from check_private_content import problems
from conftest import ROOT


def test_problem_detection():
    assert problems(["private/rulebooks/core.pdf"])
    assert problems(["docs/some-module.pdf"])
    assert problems(["services/api/shadowdark.profile.json"])
    assert not problems(["fixtures/synthetic_adventures/lantern_under_saltmere.pdf", "docs/architecture.md"])


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_git_index_is_clean():
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.decode()
    assert problems([p for p in tracked.split("\0") if p]) == []
