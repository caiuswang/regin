"""Why a topic ref is absent from the working tree.

Absence alone does not mean the anchor is dead. A path carried by HEAD's own
tree, or by the tip of a branch *whose work has not landed here*, belongs to
work this checkout simply isn't showing; a path this commit stages as removed
is dead by intent whatever other trees carry.

The verdict lives here rather than in one caller because four layers need the
same answer and must not disagree: `scan.validate` (the whole-graph gate),
`validation.audit_graph` (the authoring gate), and the two remediations that
*delete* a ref on the strength of one of those findings — `bulk_fix` behind the
audit panel's one-click strip, and `apply._filter_dead_refs` behind the
`drop_dead_refs` option.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from lib.topics.core import is_dir_ref
from lib.activity_log import get_activity_logger


ABSENT_REMOVED = "removed"
ABSENT_ELSEWHERE = "elsewhere"
ABSENT_UNPROVABLE = "unprovable"
ABSENT_DEAD = "dead"

#: The verdicts no remediation may delete on. `elsewhere` is alive at a commit
#: this working tree is not showing; `unprovable` is a path git never got to
#: answer for, and
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


#: Per-process memo of tip-scan verdicts, keyed by repo *and* the resolved
#: oids of HEAD and every tip — so any ref that moves is a different key and
#: nothing stale can be served. Only exact scans are stored (see
#: `_scan_tips`), because a degraded scan's verdict depends on how many other
#: paths shared its batch and would be wrong for a path asked about alone.
_TIP_SCAN_CACHE: dict[tuple[str, str, tuple[str, ...]], dict[str, str]] = {}

#: Which tips have landed, at the same repo/HEAD/ref-listing key and under the
#: same lock and bound as the scan memo above (see `_landed_tips`).
_LANDED_CACHE: dict[tuple[str, str, tuple[str, ...]], frozenset[str]] = {}

#: The web server answers `/diff` and `/audit` from a thread pool, so every
#: read and write of the cache above is serialised — an unguarded
#: check-then-evict lets two threads pick the same victim key and raise out of
#: an endpoint that has nothing to do with caching.
_TIP_SCAN_LOCK = threading.Lock()

#: Enough for the pre/post audit pair of one request plus a neighbouring repo.
#: The cache exists to collapse the repeats inside a single request, not to
#: outlive it, so a long-lived server holds a bounded handful of entries.
_TIP_SCAN_CACHE_MAX = 4

#: A server parked on one branch would otherwise accumulate every path ever
#: asked about into a single entry, which the key count alone does not bound.
_TIP_SCAN_PATHS_MAX = 4096


def _paths_on_other_branches(
    repo: Path, paths: set[str],
) -> tuple[set[str], set[str]]:
    """`(found, unprovable)` — the subset of `paths` carried by a tree that
    still vouches for them (see `_vouching_trees`), and the subset git never
    answered for.

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

    Three cheap git calls resolve the tips; everything after them is either
    memoised from an earlier call at these same oids or answered by
    `_scan_tips` — one batch where git will take one, the per-tip sweep where
    it will not.
    """
    try:
        head = _git_out(repo, "rev-parse", "HEAD").strip()
        tips = _vouching_trees(repo, head)
    except (subprocess.CalledProcessError, OSError):
        return set(), set(paths)

    verdicts = _memoised(repo, head, tips)
    unknown = paths - verdicts.keys()
    if unknown:
        exact, scanned = _scan_tips(repo, tips, unknown)
        verdicts.update(scanned)
        if exact:
            _memoise(repo, head, tips, scanned)
    return (
        {p for p in paths if verdicts[p] == ABSENT_ELSEWHERE},
        {p for p in paths if verdicts[p] == ABSENT_UNPROVABLE},
    )


def _vouching_trees(repo: Path, head: str) -> tuple[str, ...]:
    """The trees entitled to vouch for a path missing from the working tree:
    HEAD's own, then every branch tip whose work has not landed in it.

    HEAD is one of them because "absent from the working tree" is not the same
    as "not tracked here" — a sparse checkout, a `skip-worktree` bit or an
    unstaged `rm` all hide a path this very commit still carries, and the
    anchor for it is alive. That case used to be covered by accident, by
    whichever merged sibling happened to carry the path too; asking HEAD
    directly is what keeps it covered now that merged tips are retired. A path
    deleted *and committed* is gone from HEAD's tree as well, so the state this
    module exists to judge is untouched. It also means the scan is never
    treeless while git can resolve HEAD, so a spelling git refuses as a
    pathspec still reaches the sweep that reports it `unprovable`.

    A tip reachable from HEAD has already contributed everything it carries, so
    a path it lists that is nonetheless absent here was deleted *after* that
    merge — dead by intent, not checked out elsewhere. Left unfiltered, one
    merged branch nobody pruned kept such an anchor labelled "clears when it
    merges" long after it had (CAI-37), with no remediation willing to touch
    it. Dropping them also shrinks the scan: 121 tips to 32 on regin.

    Reachability is what "landed" means here, so a branch squash-merged or
    rebased in keeps vouching — its tip is no ancestor of HEAD, whatever became
    of its content. Those states are no worse than before this filter existed;
    ending them needs a patch-level comparison, not a walk (CAI-98).

    Landed tips are *subtracted* rather than excluded by asking git for
    `--no-merged` outright, because a ref git cannot read is absent from either
    listing — it is neither provably merged nor provably not. Asking for the
    unmerged set would therefore drop it silently, and losing a tip that way is
    how the scan goes blind without knowing it: nothing reports the tip, and
    every path it might have carried comes back `dead` — the deletable verdict
    — on the strength of a ref nobody could open (CAI-25, CAI-36). Subtracting
    keeps such a tip in the scan, where the existing unreadability handling
    turns it into `unprovable` instead.
    """
    oids = tuple(dict.fromkeys(_git_out(
        repo, "for-each-ref", "--format=%(objectname)",
        "refs/heads", "refs/remotes").split()))
    landed = _landed_tips(repo, head, oids)
    return (head, *(oid for oid in oids
                    if oid != head and oid not in landed))


def _landed_tips(
    repo: Path, head: str, oids: tuple[str, ...],
) -> frozenset[str]:
    """Which tips are reachable from HEAD, memoised on the listing they came
    from.

    Unlike the two listings around it, this one is a reachability walk — ~12 ms
    against regin's 121 tips, where the plain listing is ~10 ms and the tip
    scan it feeds is often already answered from `_TIP_SCAN_CACHE`. Uncached it
    would become the entire cost of the second audit of a request, which is the
    cost CAI-36 exists to have removed.

    Serving it stale is safe in both directions. A tip is only dropped from the
    scan while it is believed landed, and a landed tip's tree is one HEAD
    already contains; the other way round it is merely scanned when it need not
    be, for the undeletable verdict. Any answer that could change — a merge, a
    push, a deleted branch — moves HEAD or the listing, and both are in the
    key. The one thing the key cannot see is a tip's *readability* changing
    under it: delete a landed tip's object mid-request and a warm entry keeps
    calling it landed, where a cold one would find it unreadable and withhold
    `dead` for the graph. That is the correct verdict either way — the tip did
    land, so its tree is HEAD's — but it is the module's one `dead` that
    survives a git object nobody can read, and it is deliberate.
    """
    key = _memo_key(repo, head, oids)
    with _TIP_SCAN_LOCK:
        cached = _LANDED_CACHE.get(key)
    if cached is not None:
        return cached
    landed = frozenset(_git_out(
        repo, "for-each-ref", "--merged", "HEAD", "--format=%(objectname)",
        "refs/heads", "refs/remotes").split())
    with _TIP_SCAN_LOCK:
        while len(_LANDED_CACHE) >= _TIP_SCAN_CACHE_MAX:
            _LANDED_CACHE.pop(next(iter(_LANDED_CACHE)), None)
        _LANDED_CACHE[key] = landed
    return landed


def _memo_key(
    repo: Path, head: str, tips: tuple[str, ...],
) -> tuple[str, str, tuple[str, ...]]:
    """Resolved, so the same repo reached as `.` and as an absolute path is one
    entry rather than two of the very few the cache holds."""
    return (str(repo.resolve()), head, tips)


def _memoised(
    repo: Path, head: str, tips: tuple[str, ...],
) -> dict[str, str]:
    """A *snapshot* of what is already known for this repo at these tip oids.

    A copy rather than the live entry: the caller mutates what it gets back,
    and another thread iterating the same dict mid-update raises.
    """
    with _TIP_SCAN_LOCK:
        return dict(_TIP_SCAN_CACHE.get(_memo_key(repo, head, tips), {}))


def _memoise(
    repo: Path, head: str, tips: tuple[str, ...], verdicts: dict[str, str],
) -> None:
    """Record verdicts so the second audit of a request answers from the
    first's work — two cheap `rev-parse`/`for-each-ref` calls and no tree reads
    at all."""
    key = _memo_key(repo, head, tips)
    with _TIP_SCAN_LOCK:
        entry = _TIP_SCAN_CACHE.get(key)
        if entry is None:
            while len(_TIP_SCAN_CACHE) >= _TIP_SCAN_CACHE_MAX:
                _TIP_SCAN_CACHE.pop(next(iter(_TIP_SCAN_CACHE)), None)
            entry = _TIP_SCAN_CACHE[key] = {}
        elif len(entry) >= _TIP_SCAN_PATHS_MAX:
            entry.clear()
        entry.update(verdicts)


def _scan_tips(
    repo: Path, tips: tuple[str, ...], paths: set[str],
) -> tuple[bool, dict[str, str]]:
    """`(exact, verdicts)` for paths no cached scan has answered yet.

    `exact` is what makes the memo above safe to write: only the batched
    lookup answers each path independently of the others, so only its verdicts
    are a function of `(repo, tips, path)` alone. The sweep's are not — which
    of two paths gets probed for a refused spelling depends on how many paths
    shared the batch (`_MAX_PROBED_PATHS`), so caching one would hand a later,
    smaller batch a verdict that was never asked about *that* path. Getting
    that backwards is the deletable direction: a cached `dead` is auto-fixable
    and the anchor is gone before anyone re-asks.

    No trees at all cannot happen while `_vouching_trees` can resolve HEAD, and
    the answer is `unprovable` rather than `dead` for the same reason: a scan
    that asked nothing has proven nothing, and only one of the two verdicts is
    recoverable if that ever stops being unreachable.
    """
    if not tips:
        return True, dict.fromkeys(paths, ABSENT_UNPROVABLE)
    batched = _batched_tip_lookup(repo, tips, paths)
    if batched is not None:
        return True, batched
    return False, _sweep_tips(repo, tips, paths)


def _sweep_tips(
    repo: Path, tips: tuple[str, ...], paths: set[str],
) -> dict[str, str]:
    """One `ls-tree` per tip until every path is accounted for — the fallback
    for whatever the batched lookup could not stomach. Its diagnostics are the
    reason it survives: it is the only path that can tell a spelling git
    refuses from one no tree carries."""
    found: set[str] = set()
    unprovable: set[str] = set()
    blind = False
    for oid in tips:
        remaining = paths - found - unprovable
        if not remaining:
            break
        listed, refused, readable = _list_tree(repo, oid, remaining)
        blind = blind or not readable
        unprovable |= refused
        found |= {path for path in remaining - refused
                  if _listed_covers(path, listed)}
    return _verdicts(paths, found, unprovable, blind)


def _verdicts(
    paths: set[str], found: set[str], unprovable: set[str], blind: bool,
) -> dict[str, str]:
    """A path no *readable* tip carried is only dead if every tip got to
    answer; once one came back unreadable its silence proves nothing, so the
    default flips from the deletable verdict to the undeletable one."""
    absent = ABSENT_UNPROVABLE if blind else ABSENT_DEAD
    return {
        path: ABSENT_ELSEWHERE if path in found
        else ABSENT_UNPROVABLE if path in unprovable
        else absent
        for path in paths
    }


#: Object types `cat-file --batch-check` reports for something that exists at
#: `<tip>:<path>`: a file, or a directory (what a dir ref names). A gitlink is
#: the exception — git answers `<oid> submodule`, with no size — so it is
#: matched separately rather than listed here.
_GIT_OBJECT_TYPES = frozenset({"blob", "tree", "commit", "tag"})
_GIT_GITLINK_TYPE = "submodule"

#: Ceiling on `tips × paths` queries in one batch, past which the sweep's
#: early exit is the better bet than a multi-megabyte pipe. Well clear of a
#: real graph: regin's 120 tips would need 400+ absent refs to reach it.
_MAX_BATCH_QUERIES = 50_000


def _batched_tip_lookup(
    repo: Path, tips: tuple[str, ...], paths: set[str],
) -> dict[str, str] | None:
    """Every `(tip, path)` membership question in **one** `cat-file` call, or
    `None` when git would not answer them that way.

    The sweep asks each tip its own `ls-tree`, and stops early once every path
    is accounted for — so it is cheap exactly when every absent ref lives on
    some branch, and worst when one does not: proving *no* tip carries a path
    costs a subprocess per tip, ~1 s on a repo with 120 of them, and that is
    the state the audit panel exists to help resolve (CAI-36). `cat-file
    --batch-check` takes the whole cross-product on one stdin, so the same
    proof costs one process regardless of branch count.

    Only its *positive* answers stand on their own. `missing` is ambiguous —
    the tip may not carry the path, or an object on the way to it may be
    unreadable, and git reports both with exit 0 and an empty stderr — so
    before any path is called dead, `_tips_fully_readable` has to rule the
    second reading out. A resolved object needs no such corroboration: git
    could only answer with one by reading it.

    Returns `None` — deferring to the sweep with its behaviour intact —
    whenever the batch cannot be trusted rather than guessing: a path git
    refuses as a revision (`../outside.py`) kills the whole run mid-stream, a
    path containing a newline would make the reply unparseable, and any record
    that is neither a resolved object nor `missing` is an answer this parser
    does not know how to read.

    One cost the sweep did not have: on a partial clone, resolving a path makes
    git lazily fetch the object, where `ls-tree` only ever needed the tree. The
    verdict is unaffected either way — a fetch that fails takes the batch down
    to the sweep, and one suppressed by `GIT_NO_LAZY_FETCH` also fails the
    readability corroboration, so the answer degrades to `unprovable` rather
    than to `dead`.
    """
    pairs = _batch_pairs(tips, sorted(paths))
    if pairs is None:
        return None
    types = _record_types(_cat_file_batch(
        repo, [f"{oid}:{path}" for oid, path in pairs]))
    if types is None:
        return None
    found = {path for (_, path), kind in zip(pairs, types)
             if _kind_covers(path, kind)}
    return _verdicts(
        paths, found, unprovable=set(),
        blind=bool(paths - found) and not _tips_fully_readable(repo, tips),
    )


def _kind_covers(path: str, kind: str) -> bool:
    """Whether what git resolved at `<tip>:<path>` is what the ref promised.

    The sweep's `ls-tree -r` only ever lists files, so a ref spelled without a
    trailing `/` never matched a directory there, and a dir ref matched by way
    of its *members*. Resolving the path directly would quietly answer both
    differently, so the kind is checked to keep the two strategies returning
    the same verdict — this is an optimisation, not a change of policy.
    """
    return bool(kind) and (is_dir_ref(path) or kind != "tree")


def _tips_fully_readable(repo: Path, tips: tuple[str, ...]) -> bool:
    """Whether every object reachable from every tip is actually in the store.

    This is what makes the batch's silence mean something. `rev-list --objects`
    walks each tip's whole tree and dies on the first object it cannot read, so
    one call settles for all tips at once what the sweep learns a tip at a time
    — and it is only worth making when some path is otherwise about to be
    called dead.

    Blobs are deliberately *not* filtered out. A missing blob produces the same
    false `missing` from `cat-file` as a missing tree, while `ls-tree` never
    reads it and would have listed the path — so `--filter=blob:none` would
    leave exactly half of this hole open, and the half it left would report a
    checked-out-elsewhere anchor as dead.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--objects", "--no-walk",
             "--stdin"],
            input="\n".join(tips), capture_output=True, text=True,
        )
    except OSError:
        return False
    if proc.returncode != 0:
        get_activity_logger("topics").error(
            "topic_ref_tip_store_incomplete",
            repo_path=str(repo), tip_count=len(tips),
            error=proc.stderr.strip()[:200],
        )
    return proc.returncode == 0


def _batch_pairs(
    tips: tuple[str, ...], ordered: list[str],
) -> list[tuple[str, str]] | None:
    """The `(tip, path)` cross-product, or `None` if it is not worth — or not
    safe — to ask as one batch: a path carrying a newline the reply could not
    be split on, a path git resolves by rules of its own rather than as a plain
    tree entry, or a product big enough that the sweep's early exit beats the
    pipe."""
    if any(_unbatchable(path) for path in ordered):
        return None
    pairs = [(oid, path) for oid in tips for path in ordered]
    return None if len(pairs) > _MAX_BATCH_QUERIES else pairs


def _unbatchable(path: str) -> bool:
    """A spelling `cat-file` would answer for, but not about the tree entry we
    are asking about.

    `<tip>:/etc/passwd` and `<tip>:..` come back a flat `missing` — which reads
    as *dead*, the deletable verdict — where `ls-tree` refuses them outright
    and the sweep records the honest `unprovable`. These are the non-canonical
    spellings `validation._noncanonical_ref_issue` already rejects on the
    authoring path, but `apply._filter_dead_refs` classifies without that
    guard, so the sweep has to keep answering for them.
    """
    if "\n" in path or "\0" in path or path.startswith("/"):
        return True
    body = path[:-1] if is_dir_ref(path) else path
    return not body or bool({"", ".", ".."} & set(body.split("/")))


def _record_types(records: list[str] | None) -> list[str] | None:
    """One object type per record — `""` where git resolved nothing — or
    `None` if any of them, or the call that produced them, is something this
    parser cannot read."""
    if records is None:
        return None
    types = [_record_type(record) for record in records]
    return None if any(kind is None for kind in types) else types


def _cat_file_batch(repo: Path, queries: list[str]) -> list[str] | None:
    """One reply line per query, in order, or `None` if git bailed.

    `-z` makes the *input* NUL-delimited so a path with a space survives
    verbatim; the reply stays newline-delimited, which is only unambiguous
    because the caller has already excluded paths containing one.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "--batch-check", "-z"],
            input="\0".join(queries) + "\0",
            capture_output=True, text=True,
        )
    except OSError:
        return None
    records = proc.stdout.splitlines()
    if proc.returncode != 0 or len(records) != len(queries):
        return None
    return records


def _record_type(record: str) -> str | None:
    """The type git resolved, `""` for a record saying it resolved nothing, or
    `None` for one this parser has no reading for.

    A resolved reply is exactly `<oid> <type> <size>`, or `<oid> submodule` for
    a gitlink, whose target commit lives in another store and so has no size
    here; an unresolved one echoes the query, which may itself contain spaces,
    so the shapes are told apart by the resolved forms.
    """
    fields = record.split(" ")
    if (len(fields) == 3 and fields[1] in _GIT_OBJECT_TYPES
            and fields[2].isdigit()):
        return fields[1]
    if len(fields) == 2 and fields[1] == _GIT_GITLINK_TYPE:
        return _GIT_GITLINK_TYPE
    return "" if record.endswith(" missing") else None


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
