"""Invariant 1 — every build slice of feature 140 merged back into `f140/integration`.

Each slice merges its branch into the integration branch as the precondition of
reporting finished, so integrate is a *check*: a slice branch whose tip never
reached the integration branch, or that carries no commit of its own, is
invisible until something asks. Read from git alone — the task rows live in a
per-checkout DB.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INTEGRATION = "f140/integration"
BASE = "master"
BUILD_SLICES = (1, 2, 3, 4, 5, 6, 7)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=60)


def _has_ref(ref: str) -> bool:
    return _git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}").returncode == 0


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    proc = _git("merge-base", "--is-ancestor", ancestor, descendant)
    if proc.returncode not in (0, 1):
        pytest.skip(f"git cannot order {ancestor} and {descendant}: {proc.stderr.strip()}")
    return proc.returncode == 0


def test_every_slice_merged_back():
    if not _has_ref(INTEGRATION):
        pytest.skip(f"{INTEGRATION} is not in this checkout")

    branches = [f"f140/s{n}" for n in BUILD_SLICES if _has_ref(f"refs/heads/f140/s{n}")]
    if not branches:
        pytest.skip("no f140 slice branches in this checkout")

    unmerged = [b for b in branches if not _is_ancestor(b, INTEGRATION)]
    assert not unmerged, f"slice branches not merged into {INTEGRATION}: " + ", ".join(unmerged)

    if _is_ancestor(INTEGRATION, BASE):
        return
    empty = [b for b in branches
             if not _git("log", "--no-merges", "--format=%h", f"{BASE}..{b}").stdout.strip()]
    assert not empty, "slices with no commit of their own: " + ", ".join(empty)
