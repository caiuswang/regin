"""Mechanical (high-confidence) drift detection: follow git renames.

When code moves, the topic graph's refs and agent memory's path references
point at files that no longer exist. A git-tracked rename is a
*high-confidence* signal — unlike a bare dead path, we know exactly where
the file went — so we rewrite the reference in place rather than flagging it
stale. Staleness touches a memory's *veracity*; a rename does not (the lesson
and the code it names are both intact, just relocated), so the rewrite leaves
veracity untouched — keeping the strength / veracity / importance axes
orthogonal.

Two surfaces, both writing only the safe side of the trust boundary:
  * topic refs   -> rewritten into the gitignored `topic.local.json` overlay,
    never the human-approved `topic.json`;
  * memory bodies -> the named path is rewritten, veracity untouched.

Everything is gated on `settings.topic_evolution.mechanical_autoapply` (off by
default) and best-effort: a git failure must never break the commit hook or a
reflect pass. The rewrites are naturally idempotent — once the old path is
gone there is nothing left to follow on a second run.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from lib.activity_log import get_activity_logger
from lib.settings import settings

log = get_activity_logger("topics")

# Bound the history scan reflect uses to resolve a missing path's rename — a
# whole-history walk on a large repo is wasteful, and a rename old enough to
# fall outside this window is old enough that flagging it stale is fine.
_HISTORY_MAX_COMMITS = 2000


def parse_rename_status(lines: list[str]) -> dict[str, str]:
    """Map `old -> new` from `git diff/log --name-status -M` output. Only
    rename rows (`R<score>\\told\\tnew`) contribute; every other status line is
    ignored. Pure + deterministic for unit testing."""
    out: dict[str, str] = {}
    for line in lines:
        parts = line.split("\t")
        if len(parts) == 3 and parts[0][:1] == "R":
            old, new = parts[1], parts[2]
            if old and new:
                out[old] = new
    return out


def _git(repo_path: str | Path, args: list[str]) -> Optional[list[str]]:
    """Run git, returning stdout lines, or None on any failure — drift is
    best-effort and must never raise into the commit hook / reflect pass."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path)] + args,
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("drift_git_failed", args=args, exc_info=exc)
        return None
    if result.returncode != 0:
        log.error("drift_git_nonzero", args=args,
                  stderr=result.stderr.strip()[:300])
        return None
    return [ln for ln in result.stdout.splitlines() if ln]


def renames_between(repo_path: str | Path, base: str, head: str) -> dict[str, str]:
    """The `old -> new` rename map between two commits (`git diff -M`). Empty
    when there were no renames or git couldn't run."""
    lines = _git(repo_path, ["diff", "-M", "--name-status", base, head])
    return parse_rename_status(lines or [])


def deletions_between(repo_path: str | Path, base: str, head: str) -> set[str]:
    """Paths deleted (not renamed) between two commits — the genuine-staleness
    signal a rename is not. Empty when none or git couldn't run."""
    lines = _git(repo_path, ["diff", "--diff-filter=D", "--name-only", base, head])
    return set(lines or [])


def _resolve_chain(rename_map: dict[str, str], start: str) -> Optional[str]:
    """Follow a rename chain (A->B, B->C) to its end, guarding cycles. Returns
    the final path, or None when `start` was never renamed."""
    if start not in rename_map:
        return None
    seen = {start}
    current = rename_map[start]
    while current in rename_map and current not in seen:
        seen.add(current)
        current = rename_map[current]
    return current


def renames_from_history(repo_path: str | Path,
                         paths: set[str]) -> dict[str, str]:
    """For each currently-missing path, find where git history says it moved
    to. Scans the last `_HISTORY_MAX_COMMITS` rename commits, builds the full
    rename map, then chases each requested path's chain to a target that
    actually exists on disk now. Only resolvable, still-present targets are
    returned."""
    if not paths:
        return {}
    lines = _git(repo_path, [
        "log", "-M", "--diff-filter=R", "--name-status",
        f"-n{_HISTORY_MAX_COMMITS}", "--pretty=format:"])
    rename_map = parse_rename_status(lines or [])
    root = Path(repo_path)
    out: dict[str, str] = {}
    for old in paths:
        final = _resolve_chain(rename_map, old)
        if final and final != old and (root / final).is_file():
            out[old] = final
    return out


# ── topic refs (overlay only) ─────────────────────────────────


def _rewrite_topic(topic: dict[str, Any], renames: dict[str, str]) -> bool:
    """Rewrite a topic's renamed ref paths in place, preserving role and
    dropping a rename whose target is already a ref. Returns whether it
    changed."""
    refs = [r for r in topic.get("refs", []) if isinstance(r, dict)]
    present = {r.get("path") for r in refs}
    changed = False
    new_refs: list[dict[str, Any]] = []
    for ref in refs:
        target = renames.get(ref.get("path"))
        if target is None:
            new_refs.append(ref)
            continue
        changed = True
        if target in present:  # target already a ref → drop the duplicate
            continue
        present.add(target)
        rewritten = dict(ref)
        rewritten["path"] = target
        new_refs.append(rewritten)
    if changed:
        topic["refs"] = new_refs
    return changed


def rewrite_topic_refs(repo_path: str | Path,
                       renames: dict[str, str]) -> list[str]:
    """Follow `renames` into every topic's refs, persisting touched topics to
    the gitignored `topic.local.json` overlay (never `topic.json`). Returns
    the touched topic ids."""
    from lib.topics.core import load_local_graph, save_local_graph
    from lib.topics.graph_io import (load_authoritative_graph,
                                     sync_snapshot_from_disk)
    if not renames:
        return []
    graph = load_authoritative_graph(repo_path)
    touched = [tid for tid, topic in graph.get("topics", {}).items()
               if _rewrite_topic(topic, renames)]
    if not touched:
        return []
    overlay = load_local_graph(repo_path)
    for tid in touched:
        overlay["topics"][tid] = graph["topics"][tid]
    save_local_graph(repo_path, overlay)
    sync_snapshot_from_disk(repo_path, reason="drift")
    return touched


# ── memory bodies (veracity untouched) ────────────────────────

# A path-continuation char — what, if it abuts the match, means the old path is
# really part of a LONGER path and must NOT be rewritten. Deliberately excludes
# `.`: a path commonly ends a sentence (`lib/old.py.`), and the path itself
# already carries its extension, so a trailing `.` is punctuation, not part of
# the path. This boundary stops `lib/old.py` from corrupting `lib/old.pyc`
# (trailing `c`), `xsrc/app.py` (leading `x`), or `tests/src/app.py` (leading
# `/`) — the substring-collision trap — while still matching `lib/old.py.` and
# `lib/old.py)`.
_PATH_CHAR = r"[\w/-]"


def _boundary_re(old: str) -> "re.Pattern[str]":
    return re.compile(rf"(?<!{_PATH_CHAR}){re.escape(old)}(?!{_PATH_CHAR})")


def rewrite_memory_body(store, mem: dict, renames: dict[str, str], *,
                        dry_run: bool) -> bool:
    """Rewrite any renamed path named in one memory's body. Veracity is left
    untouched — a rename is not a staleness signal. Records a `ref_renamed`
    validation. Returns whether it rewrote. Idempotent: the old path is gone
    afterwards, so a re-run is a no-op. Matches on path boundaries so a
    rewrite of `a/b.py` never mangles `a/b.pyc` or `x/a/b.py`."""
    body = mem.get("body") or ""
    applied: list[str] = []
    for old, new in renames.items():
        body, n = _boundary_re(old).subn(new, body)
        if n:
            applied.append(f"{old} -> {new}")
    if not applied:
        return False
    if dry_run:
        return True
    store.update(mem["id"], body=body)
    store.record_validation(mem["id"], validator="drift", action="ref_renamed",
                            note="; ".join(applied[:5]))
    return True


def rewrite_memory_refs(store, renames: dict[str, str], *,
                        dry_run: bool = False) -> int:
    """Follow `renames` into every active memory body. Returns rows rewritten."""
    if not renames:
        return 0
    rows = store.list_memories(status="active", include_tests=True, limit=10_000)
    return sum(1 for mem in rows
               if rewrite_memory_body(store, mem, renames, dry_run=dry_run))


# ── deletion → cascade staleness onto linked memories ─────────


def cascade_deletions(repo_path: str | Path, store,
                      deletions: set[str]) -> int:
    """For each topic whose refs include a *deleted* (not renamed) file, cascade
    staleness onto its linked memories (`veracity true→unknown`). A deletion is
    genuine staleness — unlike a rename, which Phase 1 follows in place. Returns
    the number of memories demoted."""
    if not deletions:
        return 0
    from lib.memory.topic_cascade import cascade_topic_stale
    from lib.topics.graph_io import load_authoritative_graph
    graph = load_authoritative_graph(repo_path)
    demoted = 0
    for tid, topic in graph.get("topics", {}).items():
        refs = [r for r in topic.get("refs", []) if isinstance(r, dict)]
        if any(r.get("path") in deletions for r in refs):
            demoted += cascade_topic_stale(store, tid, reason="ref_deleted")
    return demoted


# ── orchestrator ──────────────────────────────────────────────


def run_mechanical_drift(repo_path: str | Path, *, base: str = "HEAD~1",
                         head: str = "HEAD") -> dict[str, Any]:
    """Commit-time mechanical drift between `base` and `head`: follow renames
    into topic refs + memory paths, and cascade genuine ref deletions onto the
    affected topics' linked memories. Gated on `mechanical_autoapply` (a no-op
    dict when off) and fully best-effort — never raises into the git hook."""
    if not settings.topic_evolution.mechanical_autoapply:
        return {"enabled": False, "renames": 0, "topics_rewritten": 0,
                "memories_rewritten": 0, "memories_staled": 0}
    try:
        from lib.memory import get_store
        store = get_store()
        renames = renames_between(repo_path, base, head)
        topics = rewrite_topic_refs(repo_path, renames)
        memories = rewrite_memory_refs(store, renames)
        deletions = deletions_between(repo_path, base, head) - set(renames)
        staled = cascade_deletions(repo_path, store, deletions)
        log.write("mechanical_drift_applied", renames=len(renames),
                  topics_rewritten=len(topics), memories_rewritten=memories,
                  memories_staled=staled)
        return {"enabled": True, "renames": len(renames),
                "topics_rewritten": len(topics), "memories_rewritten": memories,
                "memories_staled": staled}
    except Exception:  # noqa: BLE001 - drift must never break the commit hook
        log.error("mechanical_drift_failed", exc_info=True)
        return {"enabled": True, "renames": 0, "topics_rewritten": 0,
                "memories_rewritten": 0, "memories_staled": 0, "error": True}


__all__ = [
    "parse_rename_status", "renames_between", "deletions_between",
    "renames_from_history", "rewrite_topic_refs", "rewrite_memory_refs",
    "rewrite_memory_body", "cascade_deletions", "run_mechanical_drift",
]
