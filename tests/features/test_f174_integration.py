"""S5 — every build slice of feature 174 merged back into `f174/integration`.

Each slice merges its branch into the integration branch as the precondition of
reporting finished, so integrate is a *check*: a slice branch whose tip never
reached the integration branch, or that landed no commit of its own, is
invisible until something asks. Read from git alone — the task rows live in a
per-checkout DB.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INTEGRATION = "f174/integration"

#: S1–S4 build; S5 (this check) lands only its own test, S6/S7 only verify.
BUILD_SLICES = (1, 2, 3, 4)

#: `F174 S3: …`. `Merge branch …` commits are excluded by `--no-merges`, so a
#: merge never stands in for work.
LANDED_IN_SUBJECT = re.compile(r"^F174 S(\d+):")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=60)


def _has_ref(ref: str) -> bool:
    return _git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}").returncode == 0


def commits_per_slice() -> dict[int, int]:
    """Slice id → non-merge commits reachable from the integration branch that name it.

    Walked with no fork point: once master merges the integration branch a
    `master..` range empties and every slice would read as empty-handed
    exactly when the feature landed.
    """
    proc = _git("log", "--no-merges", "--format=%s", INTEGRATION)
    if proc.returncode != 0:
        pytest.skip(f"git log {INTEGRATION} is not answerable here: {proc.stderr.strip()}")
    counts: dict[int, int] = {}
    for subject in proc.stdout.splitlines():
        found = LANDED_IN_SUBJECT.search(subject)
        if found:
            n = int(found.group(1))
            counts[n] = counts.get(n, 0) + 1
    return counts


def unmerged_slice_branches() -> list[str]:
    """Slice branches whose tip is not an ancestor of the integration branch.

    Only `--is-ancestor`'s exit 1 means "not merged"; any other failure is
    git not answering, and silence is not evidence either way.
    """
    unmerged = []
    for n in BUILD_SLICES:
        branch = f"f174/s{n}"
        if not _has_ref(f"refs/heads/{branch}"):
            continue
        proc = _git("merge-base", "--is-ancestor", branch, INTEGRATION)
        if proc.returncode == 1:
            unmerged.append(branch)
        elif proc.returncode != 0:
            pytest.skip(f"git cannot say whether {branch} is merged: {proc.stderr.strip()}")
    return unmerged


def _runtime_dependencies(rev: str) -> dict[str, str]:
    proc = _git("show", f"{rev}:frontend/package.json")
    if proc.returncode != 0:
        pytest.skip(f"frontend/package.json unreadable at {rev}: {proc.stderr.strip()}")
    return json.loads(proc.stdout).get("dependencies", {})


def test_every_slice_merged_back():
    if not _has_ref(INTEGRATION):
        pytest.skip(f"{INTEGRATION} is not in this checkout")

    landed = commits_per_slice()
    empty_handed = [f"S{n}" for n in BUILD_SLICES if landed.get(n, 0) == 0]
    assert not empty_handed, (
        f"slices with no commit on {INTEGRATION}: " + ", ".join(empty_handed))

    unmerged = unmerged_slice_branches()
    assert not unmerged, (
        f"slice branches with work not merged into {INTEGRATION}: "
        + ", ".join(unmerged))


def test_no_new_runtime_dependency():
    """Invariant 7: the editor ships on what `frontend/package.json` already had."""
    if not _has_ref(INTEGRATION) or not _has_ref("master"):
        pytest.skip(f"{INTEGRATION} or master is not in this checkout")
    fork = _git("merge-base", "master", INTEGRATION)
    if fork.returncode != 0:
        pytest.skip(f"no fork point between master and {INTEGRATION}")
    # The branch is shared: F176 builds on it and adds Tiptap by its own spec,
    # so the invariant is held at F174's last commit rather than the tip.
    last = _git("log", "--no-merges", "--format=%H", "-1", "-E",
                "--grep", "^F174 S[0-9]+:", INTEGRATION).stdout.strip()
    if not last:
        pytest.skip(f"no F174 slice commit on {INTEGRATION}")
    assert _runtime_dependencies(last) == _runtime_dependencies(fork.stdout.strip())
