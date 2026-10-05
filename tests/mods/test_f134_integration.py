"""S4 — the integration check: every build slice of feature 134 merged back.

Feature 134 has no `f134/integration`; each slice merges itself straight into
`f125/integration` as the precondition of reporting finished. So integrate is a
*check*, not a merge: a slice that reported done but left nothing on the
integration branch — or whose branch carries work the integration branch never
got — is invisible until something asks.

Read from git alone: the DB holding the slices' task rows is per-checkout, so a
worktree's copy would skip the check exactly where the slices were built.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INTEGRATION = "f125/integration"

#: The build slices integrate waits on. The tail after it (migrate, verify)
#: is a pair of checks that may rightly land nothing, so it is not counted.
BUILD_SLICES = (1, 2, 3)

#: `F134 S2: …` (the slice's own commit) or `Merge f134/s2: …` (its merge back).
LANDED_IN_SUBJECT = re.compile(r"^(?:Merge )?f134[ /]s(\d+)\b", re.IGNORECASE)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=60)


def _git_out(*args: str) -> str:
    proc = _git(*args)
    if proc.returncode != 0:
        pytest.skip(f"git {' '.join(args)} is not answerable here")
    return proc.stdout


def commits_per_slice() -> dict[int, int]:
    """Slice id → commits reachable from the integration branch that name it.

    Walked with no fork point, as in the f76 check: once master merges the
    integration branch a `master..` range empties and every slice would read
    as empty-handed exactly when the feature landed.
    """
    _git_out("rev-parse", "--verify", f"{INTEGRATION}^{{commit}}")
    counts: dict[int, int] = {}
    for subject in _git_out("log", "--format=%s", INTEGRATION).splitlines():
        found = LANDED_IN_SUBJECT.match(subject)
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
        branch = f"f134/s{n}"
        if _git("rev-parse", "--verify", "-q", f"refs/heads/{branch}").returncode != 0:
            continue
        proc = _git("merge-base", "--is-ancestor", branch, INTEGRATION)
        if proc.returncode == 1:
            unmerged.append(branch)
        elif proc.returncode != 0:
            pytest.skip(f"git cannot say whether {branch} is merged: {proc.stderr.strip()}")
    return unmerged


def test_every_slice_merged_back():
    landed = commits_per_slice()

    empty_handed = [f"S{n}" for n in BUILD_SLICES if landed.get(n, 0) == 0]
    assert not empty_handed, (
        f"slices with no commit on {INTEGRATION}: " + ", ".join(empty_handed))

    unmerged = unmerged_slice_branches()
    assert not unmerged, (
        f"slice branches with work not merged into {INTEGRATION}: "
        + ", ".join(unmerged))
