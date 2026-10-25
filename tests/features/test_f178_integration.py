"""S6 — every build slice of feature 178 merged back into `master`.

F178 has no integration branch: each slice forks from and merges back into
`master`. Read from git alone — the task rows live in a per-checkout DB. The
bundle half of Invariant 4 is `test_mermaid_and_lowlight_not_in_entry` in
`test_f176_integration.py`, which owns the shared `vite build` fixture.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TARGET = "master"

#: S1–S5 build; S6 (this check) and S7 only verify.
BUILD_SLICES = (1, 2, 3, 4, 5)

#: `F178 S3: …`. `--no-merges` keeps a `Merge branch 'f178/s3'` subject from
#: standing in for the slice's work.
LANDED_IN_SUBJECT = re.compile(r"^F178 S(\d+):")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=60)


def _has_ref(ref: str) -> bool:
    return _git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}").returncode == 0


def commits_per_slice() -> dict[int, int]:
    proc = _git("log", "--no-merges", "--format=%s", TARGET)
    if proc.returncode != 0:
        pytest.skip(f"git log {TARGET} is not answerable here: {proc.stderr.strip()}")
    counts: dict[int, int] = {}
    for subject in proc.stdout.splitlines():
        found = LANDED_IN_SUBJECT.search(subject)
        if found:
            n = int(found.group(1))
            counts[n] = counts.get(n, 0) + 1
    return counts


def unmerged_slice_branches() -> list[str]:
    """Only `--is-ancestor`'s exit 1 means "not merged"; any other failure is
    git not answering, and silence is not evidence either way."""
    unmerged = []
    for n in BUILD_SLICES:
        branch = f"f178/s{n}"
        if not _has_ref(f"refs/heads/{branch}"):
            continue
        proc = _git("merge-base", "--is-ancestor", branch, TARGET)
        if proc.returncode == 1:
            unmerged.append(branch)
        elif proc.returncode != 0:
            pytest.skip(f"git cannot say whether {branch} is merged: {proc.stderr.strip()}")
    return unmerged


def test_every_slice_merged_back():
    if not _has_ref(TARGET):
        pytest.skip(f"{TARGET} is not in this checkout")

    landed = commits_per_slice()
    empty_handed = [f"S{n}" for n in BUILD_SLICES if landed.get(n, 0) == 0]
    assert not empty_handed, (
        f"slices with no commit on {TARGET}: " + ", ".join(empty_handed))

    unmerged = unmerged_slice_branches()
    assert not unmerged, (
        f"slice branches with work not merged into {TARGET}: " + ", ".join(unmerged))
