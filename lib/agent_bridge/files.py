"""Agent bridge — the workspace file/directory suggestions for a session.

Powers the `/live` composer's `@`-triggered autocomplete: paths inside the
*target session's* own working directory (the pane registry's recorded `cwd`),
so a reference typed on the phone means the same thing the raw terminal would
mean by it.

Deliberately NOT git-backed: `@` must be able to name a file that was just
created and never staged, so this walks the filesystem itself. The walk is
breadth-fair (round-robin across sibling directory generators) so one huge
subtree cannot starve its siblings out of a budgeted scan, and every budget —
depth, entries scanned, result count — is bounded.

Every query is confined to that root. There is deliberately no escape hatch:
the composer is reachable by any *editor*, and a browse-anywhere query would
hand an untrusted editor a filesystem-enumeration oracle (`~/.ssh/`, `/etc/`).
A `~`- or `/`-prefixed query is just an ordinary relative one that matches
nothing, and realpath confinement keeps symlinks and `../` inside the root too.

Confinement is not the only invariant: a path-shaped query browses a directory
directly instead of walking, so it is held to what the walk would have done for
the same directory (`_browsable`) — same ignore/hidden rule on every segment,
same refusal to enter another checkout. Anything the walk would never surface —
`.git/`, `.venv/`, `node_modules/`, `__pycache__/`, a sibling worktree's tree —
must not be reachable by typing its path either. `.claude/` and its siblings
stay reachable on purpose: that allowlist exists precisely so
`@.claude/commands/…` resolves, and the walk already surfaces those files for a
plain query.

Both rules are applied to *resolved* names, and case-folded. A name-only test
has two side doors on a real machine: `NODE_MODULES/` opens `node_modules/` on
a case-insensitive filesystem, and an in-root symlink (`gitlink -> .git`) has a
perfectly ordinary name of its own.

Read-only and fail-closed: an unreadable directory, a drifted registry or a
session with no registered cwd degrades to a shorter (or empty) list, never an
exception. The route turns that into `{"files": []}`.
"""

from __future__ import annotations

import os
import stat as stat_module
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

from lib.activity_log import get_activity_logger
from lib.agent_bridge import roots

log = get_activity_logger("agent_bridge")

MAX_DEPTH = 12
MAX_ENTRIES_SCANNED = 20_000
DEFAULT_LIMIT = 30
MAX_LIMIT = 100

LISTING_CACHE_TTL_SECONDS = 8.0
LISTING_CACHE_MAX_ENTRIES = 4_000

IGNORED_DIRECTORIES = frozenset({
    "node_modules", ".git", "dist", "build", "target", "out", "coverage",
    "vendor", "__pycache__", "venv", ".venv", "env", "virtualenv",
    ".mypy_cache", ".pytest_cache",
})
# Traversed so their contents are reachable, but never suggested themselves
# (the dot-prefix rule still hides the directory row).
TRAVERSABLE_HIDDEN_DIRECTORIES = frozenset({
    ".claude", ".github", ".vscode", ".regin",
})

_NO_MATCH_TIER = 5
_UNRANKED = 1 << 62

_listing_cache: dict[str, "_Listing"] = {}


@dataclass(frozen=True)
class _Listing:
    expires_at: float
    mtime_ns: int
    ctime_ns: int
    entries: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Entry:
    name: str
    resolved: str
    visible: str
    kind: str
    depth: int


def _clamp_limit(limit) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, value))


def _kind_of(path: str) -> str | None:
    try:
        mode = os.stat(path).st_mode
    except (OSError, ValueError):
        return None
    if stat_module.S_ISDIR(mode):
        return "directory"
    return "file" if stat_module.S_ISREG(mode) else None


def _real_dir(path: str) -> str | None:
    """`path` resolved through symlinks, or None if it isn't a directory."""
    resolved = os.path.realpath(path)
    return resolved if _kind_of(resolved) == "directory" else None


def _inside(root: str, path: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _relative(base: str, path: str) -> str:
    rel = os.path.relpath(path, base)
    return rel.replace(os.sep, "/")


def _scan_dir(directory: str) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                kind = _dirent_kind(entry)
                if kind:
                    rows.append((entry.name, kind))
    except (OSError, ValueError):
        return ()
    return tuple(sorted(rows))


def _dirent_kind(entry) -> str | None:
    try:
        if entry.is_symlink():
            return "symlink"
        if entry.is_dir(follow_symlinks=False):
            return "directory"
        return "file" if entry.is_file(follow_symlinks=False) else None
    except OSError:
        return None


def _prune_cache() -> None:
    if len(_listing_cache) <= LISTING_CACHE_MAX_ENTRIES:
        return
    now = time.monotonic()
    for key in [k for k, v in _listing_cache.items() if v.expires_at <= now]:
        _listing_cache.pop(key, None)
    while len(_listing_cache) > LISTING_CACHE_MAX_ENTRIES:
        _listing_cache.pop(next(iter(_listing_cache)), None)


def _raw_entries(directory: str) -> tuple[tuple[str, str], ...]:
    """Cached `(name, kind)` listing, revalidated against the dir's mtime/ctime."""
    try:
        info = os.stat(directory)
    except (OSError, ValueError):
        return ()
    now = time.monotonic()
    cached = _listing_cache.get(directory)
    if (cached and cached.expires_at > now
            and cached.mtime_ns == info.st_mtime_ns
            and cached.ctime_ns == info.st_ctime_ns):
        return cached.entries
    entries = _scan_dir(directory)
    _listing_cache[directory] = _Listing(now + LISTING_CACHE_TTL_SECONDS,
                                         info.st_mtime_ns, info.st_ctime_ns,
                                         entries)
    _prune_cache()
    return entries


def _children(directory: str, visible_parent: str, depth: int) -> list[_Entry]:
    """Direct children of `directory`, symlinks resolved to their target kind."""
    rows = []
    for name, kind in _raw_entries(directory):
        visible = os.path.join(visible_parent, name)
        resolved = os.path.join(directory, name)
        if kind == "symlink":
            resolved = os.path.realpath(resolved)
            kind = _kind_of(resolved)
            if kind is None:
                continue
        rows.append(_Entry(name, resolved, visible, kind, depth))
    return rows


def _traversable(name: str) -> bool:
    """Would the walk ever descend into a directory of this name?

    The single rule behind both surfaces. Applying it only to the entries the
    walk yields left the ignore list bypassable by *typing* a path: `.git/`,
    `.venv/`, `node_modules/` and `__pycache__/` all browsed fine. `.` / `..`
    are refused with them — a query that has to climb to reach its target is
    naming somewhere the walk would not have gone.

    The ignore list is matched case-folded because it is a policy, not a set of
    literal names: on a case-insensitive filesystem (APFS is one) `NODE_MODULES`
    opens exactly the directory `node_modules` names, so a case-sensitive test
    hands the whole list back one shifted keystroke at a time.
    """
    if name.lower() in IGNORED_DIRECTORIES or name in ("", ".", ".."):
        return False
    if not name.startswith("."):
        return True
    return name in TRAVERSABLE_HIDDEN_DIRECTORIES


def _allowed(name: str, kind: str) -> bool:
    """The visibility rule for one entry name, given what it turned out to be."""
    if kind == "file":
        return not name.startswith(".")
    return _traversable(name)


def _discoverable(entry: _Entry) -> bool:
    """A symlink is held to its TARGET's name as well as its own: `gitlink ->
    .git` passes every test applied to `gitlink` and still resolves inside the
    root, so only the resolved name closes that side door."""
    return (_allowed(entry.name, entry.kind)
            and _allowed(os.path.basename(entry.resolved), entry.kind))


def _segment_index(segments: Sequence[str],
                   predicate: Callable[[str], bool]) -> int:
    for index, segment in enumerate(segments):
        if predicate(segment):
            return index
    return -1


def _fuzzy_score(query: str, candidate: str) -> int | None:
    """Subsequence cost (first-match offset + skipped chars), or None if absent."""
    index = 0
    first = -1
    previous = -1
    gaps = 0
    for position, char in enumerate(candidate):
        if index >= len(query) or char != query[index]:
            continue
        if first < 0:
            first = position
        if previous >= 0:
            gaps += position - previous - 1
        previous = position
        index += 1
    return first + gaps if index == len(query) and first >= 0 else None


def _tier(segments: Sequence[str], lower: str, term: str,
          fuzzy: int | None) -> tuple[int, int]:
    """(tier, segment index) — 0 exact … 4 fuzzy, 5 no match."""
    if not term:
        return 3, _UNRANKED
    if (found := _segment_index(segments, lambda s: s == term)) >= 0:
        return 0, found
    if (found := _segment_index(segments, lambda s: s.startswith(term))) >= 0:
        return 1, found
    if (found := _segment_index(segments, lambda s: term in s)) >= 0:
        return 2, found
    if lower.startswith(term):
        return 3, _UNRANKED
    return (4, _UNRANKED) if fuzzy is not None else (_NO_MATCH_TIER, _UNRANKED)


def _rank(entry: _Entry, base: str, term: str) -> tuple:
    """Sort key: tier, segment, offset, fuzzy, depth, dirs-first, path."""
    rel = _relative(base, entry.visible)
    lower = rel.lower()
    segments = [] if lower == "." else lower.split("/")
    offset = lower.find(term)
    fuzzy = _fuzzy_score(term, segments[-1] if segments else "")
    tier, segment = _tier(segments, lower, term, fuzzy)
    return (tier, segment,
            offset if offset >= 0 else _UNRANKED,
            fuzzy if fuzzy is not None else _UNRANKED,
            len(segments), 0 if entry.kind == "directory" else 1, rel)


def _ranked(entry: _Entry, base: str, term: str) -> tuple | None:
    """The sort key for a suggestable entry, or None if it is not one."""
    if entry.name.startswith("."):
        return None
    key = _rank(entry, base, term)
    return None if key[0] == _NO_MATCH_TIER else key


def _round_robin(branches: list[Iterator[_Entry]]) -> Iterator[_Entry]:
    """One entry from each live branch per pass — a huge subtree can't starve
    its siblings out of the scan budget."""
    active = branches
    while active:
        survivors = []
        for branch in active:
            entry = next(branch, None)
            if entry is None:
                continue
            survivors.append(branch)
            yield entry
        active = survivors


def _nested_repo(directory: str) -> bool:
    """Is this directory another repo's root (a clone or a git worktree)?

    Its contents belong to a different project and would swamp the host's own
    results — this repo keeps ~10 worktrees under `.claude/worktrees/`, and a
    common filename there outnumbers the real hits several-fold.
    """
    return any(name == ".git" for name, _ in _raw_entries(directory))


def _walk(entry: _Entry, root: str, visited: set[str]) -> Iterator[_Entry]:
    yield entry
    if (entry.kind != "directory" or entry.depth >= MAX_DEPTH
            or entry.resolved in visited or _nested_repo(entry.resolved)):
        return
    visited.add(entry.resolved)
    children = _children(entry.resolved, entry.visible, entry.depth + 1)
    yield from _round_robin([_walk(child, root, visited) for child in children
                             if _keeps(child, root)])


def _walkable_chain(directory: str, confine: str) -> bool:
    """Is every directory from `confine` down to `directory` one the walk enters?

    The names on the RESOLVED path, not the typed one — a symlink pointing into
    an ignored subtree (`pkglink -> node_modules/pkg`) has a perfectly ordinary
    name of its own and lands inside the root.
    """
    rel = _relative(confine, directory)
    return rel == "." or all(_traversable(segment) for segment in rel.split("/"))


def _keeps(entry: _Entry, root: str) -> bool:
    if not _inside(root, entry.resolved) or not _discoverable(entry):
        return False
    return _walkable_chain(os.path.dirname(entry.resolved), root)


def _search_tree(root: str, term: str) -> list[tuple]:
    """Budgeted breadth-fair walk of `root`, ranked against `term`."""
    visited = {root}
    branches = [_walk(child, root, visited)
                for child in _children(root, root, 1) if _keeps(child, root)]
    ranked: list[tuple] = []
    scanned = 0
    for entry in _round_robin(branches):
        scanned += 1
        key = _ranked(entry, root, term)
        if key is not None:
            ranked.append(key)
        if scanned >= MAX_ENTRIES_SCANNED:
            break
    return ranked


def _browsable(resolved: str, confine: str) -> bool:
    """Would the walk have reached the children of `resolved`?

    A browse skips the walk, so it has to re-derive what the walk would have
    done for the same directory: stay inside the root, cross no ignored or
    hidden directory, and stop at another checkout's root the way `_walk` does.
    `confine` itself is exempt from the nested-repo test — a session's own root
    is normally a repo root.
    """
    if not _inside(confine, resolved) or not _walkable_chain(resolved, confine):
        return False
    rel = _relative(confine, resolved)
    directory = confine
    for segment in ([] if rel == "." else rel.split("/")):
        directory = os.path.join(directory, segment)
        if _nested_repo(directory):
            return False
    return True


def _browse(directory: str, visible_parent: str, base: str, term: str,
            confine: str) -> list[tuple]:
    """Rank the direct children of one directory (path-shaped queries)."""
    resolved = _real_dir(directory)
    if not resolved or not _browsable(resolved, confine):
        return []
    keys = []
    for child in _children(resolved, visible_parent, 1):
        if not _keeps(child, confine):
            continue
        key = _ranked(child, base, term)
        if key is not None:
            keys.append(key)
    return keys


def _rows(keys: list[tuple], limit: int) -> list[dict]:
    """Best key per path → sorted, limited `{path, kind}` rows, root-relative."""
    best: dict[str, tuple] = {}
    for key in keys:
        rel = key[-1]
        if rel not in best or key < best[rel]:
            best[rel] = key
    return [{"path": key[-1], "kind": "directory" if key[-2] == 0 else "file"}
            for key in sorted(best.values())[:limit]]


def _split(path: str) -> tuple[str, str]:
    """(parent, search term) for a path-shaped query."""
    parent, _, term = path.rpartition("/")
    return parent, term


def _project_results(root: str, normalized: str, limit: int) -> list[dict]:
    if "/" not in normalized:
        keys = (_search_tree(root, normalized.lower()) if normalized
                else _browse(root, root, root, "", root))
        return _rows(keys, limit)
    parent, term = _split(normalized)
    if not all(_traversable(segment) for segment in parent.split("/")):
        return []
    branch = os.path.join(root, parent)
    return _rows(_browse(branch, branch, root, term.lower(), root), limit)


def _normalize(typed: str) -> str:
    """A typed query as a root-relative path.

    A leading `/` is dropped rather than honoured — `os.path.join(root, "/etc")`
    is `/etc`, and no query may name an absolute location. `~` needs no such
    care: it is never expanded, so `~/…` simply resolves under the root and
    finds nothing. Confinement backstops both.
    """
    normalized = typed
    while normalized.startswith("./"):
        normalized = normalized[2:]
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    normalized = normalized.lstrip("/")
    return "" if normalized == "." else normalized


def _confinement_root(root) -> str | None:
    """`root` as a usable search root, or None if it cannot be one.

    A missing, empty or *relative* root is refused rather than normalized: it
    would resolve against the server process's cwd — regin's own tree — and
    answer a session's `@` with files from a project it never named.

    A NUL byte is refused rather than stripped for the same reason it is in the
    query: every `os` call rejects it with a `ValueError` no `OSError` handler
    below catches, and stripping it would silently name a different path.
    """
    if not isinstance(root, str) or "\x00" in root or not os.path.isabs(root):
        return None
    return _real_dir(root)


def search_files(root: str, query: str = "", limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Ranked path suggestions under `root` for `query`. Never raises.

    A blank query lists the root's immediate children. Every other query is
    root-relative and confined to `root` by realpath, so neither a symlink nor
    a `../`, `~/…` or `/…` query can name anything outside it.

    `root` must be an absolute path; anything else yields no suggestions at all
    (see `_confinement_root`).
    """
    limit = _clamp_limit(limit)
    typed = (query or "").strip().replace("\\", "/")
    resolved = _confinement_root(root)
    if "\x00" in typed or not resolved:
        return []
    return _project_results(resolved, _normalize(typed), limit)


def list_session_files(trace_id: str, query: str = "",
                       limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Path suggestions scoped to the target session's own working directory.

    A session with no registered cwd gets an empty menu — regin's own tree is
    not a stand-in for someone else's project.
    """
    root = roots.session_cwd(trace_id)
    rows = search_files(str(root), query, limit) if root else []
    log.read("bridge_files_listed", trace_id=trace_id, count=len(rows))
    return rows
