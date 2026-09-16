"""S9 — the integration check: no slice of feature 76 landed zero commits.

Feature 76 merges by slice, not by a final big merge: each slice merges itself
back into `f76/integration` as the precondition of reporting finished (R6). So
the integration step is a *check*, and this is it — a slice that has a branch
but left nothing on the integration branch is exactly the failure the
merge-on-finish rule exists to prevent, and it is invisible until something
asks.

Read from git alone, because git is where the answer lives: the DB that holds
the slices' task rows is per-checkout, and a worktree's copy would make the
check skip precisely where the slices were built.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INTEGRATION = "f76/integration"

#: A slice's branch, e.g. `f76/s7`. Its existence is the evidence that the
#: slice ran; what the check then asks is whether anything of it is on the
#: integration branch.
SLICE_BRANCH = re.compile(r"^f76/s(\d+)$")

#: `f76 S7: …` (the slice's own commit) or `Merge f76/s7: …` (its merge back).
#: Either one proves the slice put something on the branch.
LANDED_IN_SUBJECT = re.compile(r"^(?:Merge )?f76[ /][Ss](\d+)\b")


def _git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        pytest.skip(f"git {' '.join(args)} is not answerable here")
    return proc.stdout


def commits_per_slice() -> dict[int, int]:
    """Slice id → how many commits reachable from the integration branch name it.

    Walked with no fork point on purpose. A range against master answers this
    only until the feature lands: once master merges the integration branch,
    the merge-base *is* that branch's tip, the range empties, and every slice
    reads as empty-handed — the check would go red exactly when the feature
    succeeded. A commit subject names its slice forever, so the subject is the
    anchor and reachability is the range.
    """
    _git("rev-parse", "--verify", f"{INTEGRATION}^{{commit}}")
    counts: dict[int, int] = {}
    for subject in _git("log", "--format=%s", INTEGRATION).splitlines():
        found = LANDED_IN_SUBJECT.match(subject)
        if found:
            n = int(found.group(1))
            counts[n] = counts.get(n, 0) + 1
    return counts


def slices_with_a_branch() -> set[int]:
    """Slice ids that have a branch, minus the one checked out here.

    A slice merges back as the last act of finishing, so the slice running
    this check has not merged back yet by construction — counting itself would
    make the check red for every slice that runs it. Matched by resolved SHA
    rather than by branch name: `rev-parse --abbrev-ref HEAD` answers the
    literal "HEAD" on a detached checkout, which excludes nothing.
    """
    head = _git("rev-parse", "HEAD").strip()
    found = set()
    for branch in _git("branch", "--format=%(refname:short)").split():
        match = SLICE_BRANCH.match(branch)
        if match and _git("rev-parse", branch).strip() != head:
            found.add(int(match.group(1)))
    return found


def test_no_slice_landed_zero_commits():
    landed = commits_per_slice()
    ran = slices_with_a_branch() | set(landed)
    if not ran:
        pytest.skip(f"{INTEGRATION} carries no slice of feature 76 here")

    empty_handed = sorted(n for n in ran if landed.get(n, 0) == 0)
    assert not empty_handed, (
        f"slices that ran but put no commit on {INTEGRATION}: "
        + ", ".join(f"S{n}" for n in empty_handed))
