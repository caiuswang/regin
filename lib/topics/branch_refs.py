"""Why a topic ref is absent from the working tree.

Absence alone does not mean the anchor is dead. A path carried by the tip of
some *other* branch belongs to work that simply isn't checked out; a path this
commit stages as removed is dead by intent whatever other branches carry.

The verdict lives here rather than in one caller because four layers need the
same answer and must not disagree: `scan.validate` (the whole-graph gate),
`validation.audit_graph` (the authoring gate), and the two remediations that
*delete* a ref on the strength of one of those findings — `bulk_fix` behind the
audit panel's one-click strip, and `apply._filter_dead_refs` behind the
`drop_dead_refs` option.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from lib.topics.core import is_dir_ref
from lib.activity_log import get_activity_logger


ABSENT_REMOVED = "removed"
ABSENT_ELSEWHERE = "elsewhere"
ABSENT_DEAD = "dead"


def classify_absent_paths(
    repo_path: str | Path, paths: set[str],
) -> dict[str, str]:
    """Map each absent path to `ABSENT_REMOVED` / `ABSENT_ELSEWHERE` / `ABSENT_DEAD`.

    One git pass for the whole batch, so a graph with N absent refs costs the
    same as one. A path this commit stages as removed is `removed` whatever
    other branches still carry — the anchor has to be updated here, so it
    outranks the branch check.
    """
    if not paths:
        return {}
    repo = Path(repo_path)
    elsewhere = _paths_on_other_branches(repo, paths)
    removed = _staged_removals(repo)
    verdicts: dict[str, str] = {}
    for path in paths:
        if _removed_by_commit(path, removed):
            verdicts[path] = ABSENT_REMOVED
        elif path in elsewhere:
            verdicts[path] = ABSENT_ELSEWHERE
        else:
            verdicts[path] = ABSENT_DEAD
    return verdicts


def _removed_by_commit(path: str, removed: set[str]) -> bool:
    """Whether the staged change destroys what `path` anchors. A dir ref names
    a subtree, so it dies with its last surviving member — the caller only asks
    about refs already absent from the working tree, so any staged removal
    beneath it means the whole subtree went."""
    if is_dir_ref(path):
        return any(gone.startswith(path) for gone in removed)
    return path in removed


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def _staged_removals(repo: Path) -> set[str]:
    """Repo-relative paths this commit removes. Empty outside a commit.

    A rename counts: `git mv`ing an anchored file destroys the anchored path
    just as surely as deleting it, and rename detection would otherwise report
    it as `R` and slip past a delete-only filter. `-z` keeps paths verbatim —
    without it git applies `core.quotePath`, and a non-ASCII path comes back
    C-escaped and matches nothing.
    """
    try:
        out = _git_out(
            repo, "diff", "--cached", "-z", "--name-status", "-M", "--diff-filter=DR",
        )
    except (subprocess.CalledProcessError, OSError):
        return set()
    fields = [f for f in out.split("\0") if f]
    removed: set[str] = set()
    i = 0
    while i < len(fields):
        status = fields[i]
        # A rename record is a triple (status, old, new); a deletion is a pair.
        if status.startswith("R") and i + 2 < len(fields):
            removed.add(fields[i + 1])
            i += 3
        elif i + 1 < len(fields):
            removed.add(fields[i + 1])
            i += 2
        else:
            break
    return removed


def _paths_on_other_branches(repo: Path, paths: set[str]) -> set[str]:
    """The subset of `paths` present in the tip tree of a branch other than
    the checked-out one.

    Fails *open* throughout: whenever git cannot answer — no repo, unborn
    HEAD, an argv too long for one batch, a ref spelled as something git
    refuses to take as a pathspec (`../outside.py`, `/etc/passwd`) — the
    honest answer is "unprovable", not "dead". Reporting a path as found
    downgrades its absence to a warning; swallowing the failure instead would
    turn every absent ref in the graph into a commit-blocking error on the
    strength of a git invocation nobody saw fail. A deletion is still caught
    regardless, because the staged-removal check outranks this one.
    """
    try:
        head = _git_out(repo, "rev-parse", "HEAD").strip()
        oids = _git_out(repo, "for-each-ref", "--format=%(objectname)",
                        "refs/heads", "refs/remotes").split()
    except (subprocess.CalledProcessError, OSError):
        return set(paths)

    found: set[str] = set()
    for oid in dict.fromkeys(oids):
        remaining = paths - found
        if not remaining:
            break
        if oid == head:
            continue
        try:
            # --literal-pathspecs keeps a ref spelled like pathspec magic
            # (`:(foo)x.py`) from being interpreted; -z keeps paths verbatim.
            listed = set(_git_out(
                repo, "--literal-pathspecs", "ls-tree", "-r", "--name-only", "-z",
                oid, "--", *remaining,
            ).split("\0"))
        except (subprocess.CalledProcessError, OSError) as exc:
            get_activity_logger("topics").error(
                "topic_ref_branch_lookup_failed",
                repo_path=str(repo), tree=oid, path_count=len(remaining),
                error=str(exc),
            )
            return set(paths)
        found |= {path for path in remaining if _listed_covers(path, listed)}
    return found


def _listed_covers(path: str, listed: set[str]) -> bool:
    """A dir ref promises a subtree, so ls-tree reports its *members*, never
    the directory itself."""
    if is_dir_ref(path):
        return any(name.startswith(path) for name in listed)
    return path in listed


__all__ = [
    "ABSENT_DEAD",
    "ABSENT_ELSEWHERE",
    "ABSENT_REMOVED",
    "classify_absent_paths",
]
