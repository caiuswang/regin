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
ABSENT_UNPROVABLE = "unprovable"
ABSENT_DEAD = "dead"

#: The verdicts no remediation may delete on. `elsewhere` is alive on another
#: branch tip; `unprovable` is a path git never got to answer for, and
#: deleting on an unanswered question is how the graph loses curation nobody
#: can put back (CAI-30).
UNDELETABLE_VERDICTS = frozenset({ABSENT_ELSEWHERE, ABSENT_UNPROVABLE})


def classify_absent_paths(
    repo_path: str | Path, paths: set[str],
) -> dict[str, str]:
    """Map each absent path to `ABSENT_REMOVED` / `ABSENT_ELSEWHERE` /
    `ABSENT_UNPROVABLE` / `ABSENT_DEAD`.

    One git pass for the whole batch, so a graph with N absent refs costs the
    same as one. A path this commit stages as removed is `removed` whatever
    other branches still carry — the anchor has to be updated here, so it
    outranks the branch check. Being *found* outranks the lookup having failed
    somewhere else in the sweep: a proven answer is not weakened by a later
    tree git refused to read.
    """
    if not paths:
        return {}
    repo = Path(repo_path)
    elsewhere, unprovable = _paths_on_other_branches(repo, paths)
    removed = _staged_removals(repo)
    verdicts: dict[str, str] = {}
    for path in paths:
        if _removed_by_commit(path, removed):
            verdicts[path] = ABSENT_REMOVED
        elif path in elsewhere:
            verdicts[path] = ABSENT_ELSEWHERE
        elif path in unprovable:
            verdicts[path] = ABSENT_UNPROVABLE
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


def _paths_on_other_branches(
    repo: Path, paths: set[str],
) -> tuple[set[str], set[str]]:
    """`(found, unprovable)` — the subset of `paths` present in the tip tree of
    a branch other than the checked-out one, and the subset git never answered
    for.

    Fails *open* throughout: whenever git cannot answer — no repo, unborn
    HEAD, an argv too long for one batch, a ref spelled as something git
    refuses to take as a pathspec (`../outside.py`, `/etc/passwd`) — the
    honest answer is "unprovable", not "dead". Both halves are treated as
    non-deletable by every caller, so the fail-open guarantee is unchanged;
    splitting them only lets the callers say which one happened instead of
    telling the user a path is on a branch when there may be no branch at all.
    Swallowing the failure instead would turn every absent ref in the graph
    into a commit-blocking error on the strength of a git invocation nobody
    saw fail. A deletion is still caught regardless, because the
    staged-removal check outranks this one.

    A batch that fails is diagnosed rather than believed, so the one ref git
    refuses as a pathspec is the only one that comes back unprovable — before,
    it condemned every other absent ref in the graph with it. Such a path is
    then dropped from the sweep instead of being re-tried against every
    remaining tip: the same spelling is refused by every tree in the same repo,
    and one bad ref would otherwise add a subprocess per branch. Both verdicts
    are non-deletable, so the most that costs is which of the two warnings the
    user reads.

    A tip whose object cannot be read is the one failure that must outlive the
    sweep: it might have been the tip carrying the path, so once any tip comes
    back unreadable, "no tip listed it" stops meaning "no tip has it" and every
    path still unaccounted for is unprovable rather than dead. That is
    deliberately strict — one dangling ref withholds `dead` for the whole
    graph — because the alternative is a verdict that *deletes*, on the
    strength of git calls that all failed.
    """
    try:
        head = _git_out(repo, "rev-parse", "HEAD").strip()
        oids = _git_out(repo, "for-each-ref", "--format=%(objectname)",
                        "refs/heads", "refs/remotes").split()
    except (subprocess.CalledProcessError, OSError):
        return set(), set(paths)

    found: set[str] = set()
    unprovable: set[str] = set()
    blind = False
    for oid in dict.fromkeys(oids):
        remaining = paths - found - unprovable
        if not remaining:
            break
        if oid == head:
            continue
        listed, refused, readable = _list_tree(repo, oid, remaining)
        blind = blind or not readable
        unprovable |= refused
        found |= {path for path in remaining - refused
                  if _listed_covers(path, listed)}
    if blind:
        unprovable |= paths - found
    return found, unprovable


# Above this many absent paths, a failed batch is likelier to have died on
# argv length than on one bad spelling, and probing each one would cost a
# subprocess per absent ref on every branch tip. The pathspec-free listing has
# already answered which of them the tree carries by then; all that is given up
# is telling `unprovable` from `dead` for the rest.
_MAX_PROBED_PATHS = 64


def _list_tree(
    repo: Path, oid: str, paths: set[str],
) -> tuple[set[str], set[str], bool]:
    """`(listed, refused, readable)` for one tree.

    A failed batch says nothing about *which* argument git objected to, so the
    tree is re-read with no pathspec at all — nothing left for git to refuse.
    If that works the tip is fine and the batch died on its arguments: the
    listing answers membership for every path, and only the ones it does not
    carry are worth probing individually. If even that fails the tip itself is
    unreadable — its object is missing from the store — which is not the paths'
    fault: it refuses nothing, the remaining tips still get their say, and
    `readable=False` keeps the caller from reading their silence as proof of
    death.
    """
    try:
        return _ls_tree(repo, oid, paths), set(), True
    except (subprocess.CalledProcessError, OSError) as exc:
        get_activity_logger("topics").error(
            "topic_ref_branch_lookup_failed",
            repo_path=str(repo), tree=oid, path_count=len(paths),
            error=str(exc),
        )
    try:
        listed = _ls_tree(repo, oid, set())
    except (subprocess.CalledProcessError, OSError):
        return set(), set(), False
    missing = {p for p in paths if not _listed_covers(p, listed)}
    return listed, _refused_paths(repo, oid, missing), True


def _refused_paths(repo: Path, oid: str, paths: set[str]) -> set[str]:
    """Which of `paths` git will not take as a pathspec, one probe each."""
    if len(paths) > _MAX_PROBED_PATHS:
        return set()
    refused: set[str] = set()
    for path in paths:
        try:
            _ls_tree(repo, oid, {path})
        except (subprocess.CalledProcessError, OSError):
            refused.add(path)
    return refused


def _ls_tree(repo: Path, oid: str, paths: set[str]) -> set[str]:
    # --literal-pathspecs keeps a ref spelled like pathspec magic
    # (`:(foo)x.py`) from being interpreted; -z keeps paths verbatim.
    return set(_git_out(
        repo, "--literal-pathspecs", "ls-tree", "-r", "--name-only", "-z",
        oid, "--", *paths,
    ).split("\0"))


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
    "ABSENT_UNPROVABLE",
    "UNDELETABLE_VERDICTS",
    "classify_absent_paths",
]
