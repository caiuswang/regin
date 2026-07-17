"""ORM ↔ filesystem adapter for the approved graph.

Two-direction bridge that lets the rest of the codebase migrate at its
own pace from graph-on-disk to `GraphSnapshot`-in-ORM:

- `load_graph_from_snapshot(repo_id)` returns the same dict shape
  `lib.topics.core.load_graph(repo_path)` returns, sourced from the
  latest snapshot. Readers can swap one call for the other transparently
  (Phase D mass-replaces them).

- `export_graph_to_disk(repo_id, graph, wiki_pages)` writes the graph
  to the split dir + per-topic wiki files atomically. Called by
  `apply_diff` after each snapshot commit. Until Phase D, this keeps
  the disk copy faithful for legacy readers (CLI, hook, topic-router
  skill, pre-commit hook).

Atomicity: writes go to a `.tmp` sibling, then fsync, then rename. On
POSIX, the rename is atomic — per file. The split layout is atomic per
topic file only, so a crash mid-write can leave a mixed old/new dir
that the next read treats as disk truth. SQL commits LAST in the
cross-store protocol,
so a process crash after the file write but before the SQL commit
leaves the next read with disk≠SQL — the reconciliation guard catches
that and re-exports.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import update as sa_update
from sqlmodel import Session, select

from lib.orm import SessionLocal
from lib.orm.models import GraphSnapshot, Repo
from lib.topics.core import (
    TopicGraphError,
    _atomic_write,
    graph_exists,
    load_graph_merged,
    topic_dir,
    topic_path,
    write_graph_to_disk,
)
from lib.topics.snapshots import graph_from_snapshot, latest_snapshot, wiki_pages_from_snapshot


log = logging.getLogger(__name__)


def load_graph_from_snapshot(
    repo_id: int, *, session: Optional[Session] = None,
) -> Optional[dict[str, Any]]:
    """Return the live approved-graph dict, or None if no snapshot exists.

    None signals "this repo has never been written through `apply_diff`
    yet" — callers fall back to `lib.topics.core.load_graph(repo_path)`
    during the Phase A→D transition.
    """
    snap = latest_snapshot(repo_id, session=session)
    return graph_from_snapshot(snap) if snap is not None else None


def load_authoritative_graph(repo_path: str | Path) -> dict[str, Any]:
    """Snapshot-first reader for the approved graph.

    Three resolution outcomes:

      1. Repo row registered + has `is_latest=1` snapshot → decode it.
         If the on-disk graph has diverged from the snapshot (e.g.
         a test/CLI wrote disk directly without going through
         apply_diff), the snapshot is re-seeded from the disk content
         and the fresh state returned. This drift-detect path keeps
         the snapshot as the source of truth while allowing disk
         writes by legacy / test paths to propagate.
      2. Repo row registered but no snapshot yet → auto-seed a
         snapshot from the on-disk graph and return its graph.
      3. No Repo row at all (e.g. test fixture that never called
         `add-repo`) → disk fallback. Kept because tests use this
         path.
    """
    from sqlalchemy.exc import IntegrityError

    path_str = str(Path(repo_path).resolve())
    with SessionLocal() as s:
        repo = s.exec(select(Repo).where(Repo.path == path_str)).first()
        if repo is None or repo.id is None:
            return _load_merged_disk(repo_path)
        snap = latest_snapshot(repo.id, session=s)

    if snap is not None:
        snap_graph = graph_from_snapshot(snap)
        if graph_exists(repo_path):
            # Compare the MERGED disk graph (base + topic.local.json)
            # against the snapshot so the overlay is never invisible to the
            # drift detector — otherwise an overlay-only write would look
            # like base drift and get stomped on re-seed.
            try:
                disk_graph = _load_merged_disk(repo_path)
            except (OSError, json.JSONDecodeError, TopicGraphError):
                return snap_graph
            if _graph_hash(disk_graph) != _graph_hash(snap_graph):
                # Disk diverged — re-seed snapshot from disk so legacy
                # paths' writes propagate to the source of truth.
                try:
                    _auto_seed_snapshot(repo.id, disk_graph, repo_path)
                    log.info("re-seeded snapshot for repo=%s from drifted disk", repo.name)
                except IntegrityError:
                    pass
                return disk_graph
        return snap_graph

    # Outcome 2: no snapshot yet — auto-seed from disk (base + overlay).
    disk_graph = _load_merged_disk(repo_path)
    try:
        seed_id = _auto_seed_snapshot(repo.id, disk_graph, repo_path)
        log.info("auto-seeded snapshot id=%s for repo=%s from disk", seed_id, repo.name)
    except IntegrityError:
        with SessionLocal() as s:
            re_read = load_graph_from_snapshot(repo.id, session=s)
        return re_read if re_read is not None else disk_graph
    return disk_graph


def _auto_seed_snapshot(repo_id: int, graph: dict[str, Any], repo_path: str | Path) -> int:
    """Insert a `reason='auto_seed'` snapshot for outcome (2) above.

    Caller has just confirmed no `is_latest=1` snapshot exists for the
    repo, so this is the first row. Uses the same flush-then-insert
    pattern `apply_diff` uses to honour the partial unique index even
    when a prior snapshot exists with `is_latest=0` (which is
    unreachable from this code path but kept defensively).

    Per-topic wiki files under the repo's `wiki/` directory are ingested
    into `wiki_pages_json` so a rollback to this snapshot restores them.
    """
    import json as _json
    from datetime import datetime, timezone

    wiki_pages = _read_wiki_pages_from_disk(repo_path, graph)
    with SessionLocal() as s:
        # Demote ALL is_latest rows in one statement (defensive: a
        # transient duplicate can exist when snapshots are written in
        # rapid succession across sessions) so the new is_latest=1 INSERT
        # can't collide with the partial unique index.
        s.execute(
            sa_update(GraphSnapshot)
            .where(GraphSnapshot.repo_id == repo_id)
            .where(GraphSnapshot.is_latest == 1)
            .values(is_latest=0)
        )
        s.flush()
        snap = GraphSnapshot(
            repo_id=repo_id,
            taken_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            reason="auto_seed",
            triggering_run_id=None,
            triggering_proposal_topic_id=None,
            graph_json=_json.dumps(graph),
            wiki_pages_json=_json.dumps(wiki_pages),
            diff_summary_json=_json.dumps({"reason": "auto_seed", "via": "load_authoritative_graph"}),
            pinned=0,
            is_latest=1,
        )
        s.add(snap)
        s.commit()
        return snap.id


def _graph_hash(graph: dict[str, Any]) -> str:
    """Stable hash for drift detection. Uses sorted-keys + no indent."""
    return hashlib.sha256(
        json.dumps(graph, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _load_merged_disk(repo_path: str | Path) -> dict[str, Any]:
    """Effective on-disk graph: base split dir + ``topic.local.json``.

    The single definition of "disk" for the snapshot reader/drift detector,
    so the gitignored overlay is always part of the comparison.
    """
    return load_graph_merged(repo_path)


def _read_wiki_pages_from_disk(repo_path: str | Path, graph: dict[str, Any]) -> dict[str, str]:
    """Collect per-topic wiki bodies for a snapshot's `wiki_pages_json`.

    Returns `{topic_id: markdown}` for every `<topic_id>.md` under the
    repo's `wiki/` directory whose stem matches a topic id in `graph`.
    Stale `.md` files for topics no longer in the graph are skipped so
    a rollback never resurrects pruned content.
    """
    wiki_dir = topic_dir(repo_path) / "wiki"
    if not wiki_dir.is_dir():
        return {}
    topic_ids = set((graph.get("topics") or {}).keys())
    out: dict[str, str] = {}
    for md_file in sorted(wiki_dir.glob("*.md")):
        tid = md_file.stem
        if tid not in topic_ids:
            continue
        try:
            out[tid] = md_file.read_text(encoding="utf-8")
        except OSError:
            log.warning("skipped wiki file %s (read failed)", md_file)
    return out


def export_graph_to_disk(
    repo_path: str | Path,
    graph: dict[str, Any],
    wiki_pages: Optional[dict[str, str]] = None,
) -> Path:
    """Write `graph` to disk atomically, in whichever layout the repo is on
    (one file per topic under `topics/` + `_meta.json`).

    `wiki_pages` is the `{topic_id: markdown}` shape from `GraphSnapshot.wiki_pages_json`.
    Phase A always passes `{}` because the per-topic wiki generator is
    Phase C work — this signature is in place now so the disk-writer
    contract doesn't change later.

    Returns the path that was written (file or split dir), for logging.
    """
    target = write_graph_to_disk(repo_path, graph)
    _write_wiki_pages(repo_path, wiki_pages)
    log.debug("exported graph for %s (%d topics)", target, len(graph.get("topics", {})))
    return target


def _write_wiki_pages(repo_path: str | Path, wiki_pages: Optional[dict[str, str]]) -> None:
    """Write per-topic wiki bodies under the repo's `wiki/` directory."""
    if not wiki_pages:
        return
    from lib.topics.wiki import topic_wiki_page

    for topic_id, body in wiki_pages.items():
        if not isinstance(body, str):
            continue
        _atomic_write(topic_wiki_page(repo_path, topic_id), body)


def export_overlay_to_disk(
    repo_path: str | Path,
    prospective_graph: dict[str, Any],
    wiki_pages: Optional[dict[str, str]] = None,
) -> Path:
    """Persist a prospective graph to the local overlay, leaving the base alone.

    Splits `prospective_graph` against the git-tracked base graph:

    - topics that are new or differ from the base → written to
      `topic.local.json` (whole-topic override)
    - base topics absent from the prospective graph → recorded as
      `deleted_topics` tombstones so the merge drops them
    - topics identical to the base → omitted, keeping the overlay minimal

    `merge(base, overlay)` then reconstructs `prospective_graph` exactly,
    so `_load_merged_disk` and the snapshot stay hash-equal. The base graph
    is never touched. Per-topic wiki bodies still write under `wiki/`
    (already gitignored), matching `export_graph_to_disk`.

    Returns the overlay path that was written, for logging.
    """
    from lib.topics.core import load_graph, load_local_graph, save_local_graph, topic_local_path

    try:
        base_topics = (load_graph(repo_path).get("topics") or {})
    except TopicGraphError:
        base_topics = {}  # no base yet → every prospective topic is local
    prospective_topics = prospective_graph.get("topics") or {}

    overlay = load_local_graph(repo_path)
    overlay["topics"] = {
        tid: entry
        for tid, entry in prospective_topics.items()
        if tid not in base_topics or base_topics[tid] != entry
    }
    overlay["deleted_topics"] = sorted(
        tid for tid in base_topics if tid not in prospective_topics
    )
    save_local_graph(repo_path, overlay)

    _write_wiki_pages(repo_path, wiki_pages)
    local_path = topic_local_path(repo_path)
    log.debug(
        "exported overlay for %s (%d topics, %d tombstones)",
        local_path, len(overlay["topics"]), len(overlay["deleted_topics"]),
    )
    return local_path


def reconcile_if_drifted(  # unused until Phase D readers switch to snapshots
    repo_id: int,
    repo_path: str | Path,
    *,
    session: Optional[Session] = None,
) -> bool:
    """Re-export the on-disk graph if it doesn't match the latest snapshot.

    Called by readers in a dev/test setting to detect crash-between-commits
    states (file written, SQL rolled back, or vice-versa). Returns True
    if a re-export was needed.

    Production reads should not pay this cost on every call; callers
    can gate behind `settings.dev_mode` or call it from a startup hook.
    """
    snap = latest_snapshot(repo_id, session=session)
    if snap is None:
        return False
    snap_graph = graph_from_snapshot(snap)
    if not graph_exists(repo_path):
        export_graph_to_disk(
            repo_path, snap_graph,
            wiki_pages=wiki_pages_from_snapshot(snap),
        )
        return True
    try:
        disk_graph = _load_merged_disk(repo_path)
    except (OSError, json.JSONDecodeError, TopicGraphError):
        export_graph_to_disk(
            repo_path, snap_graph,
            wiki_pages=wiki_pages_from_snapshot(snap),
        )
        return True
    if _graph_hash(disk_graph) != _graph_hash(snap_graph):
        log.warning(
            "on-disk graph (%s) diverged from latest snapshot id=%s — re-exporting",
            repo_path, snap.id,
        )
        export_graph_to_disk(
            repo_path, snap_graph,
            wiki_pages=wiki_pages_from_snapshot(snap),
        )
        return True
    return False


def repo_path_for(repo_id: int, *, session: Optional[Session] = None) -> Optional[str]:
    """Helper for callers that have a `repo_id` but need the path."""
    def _run(s: Session) -> Optional[str]:
        repo = s.exec(select(Repo).where(Repo.id == repo_id)).first()
        return repo.path if repo is not None else None

    if session is not None:
        return _run(session)
    with SessionLocal() as s:
        return _run(s)


def sync_snapshot_from_disk(
    repo_path: str | Path,
    *,
    reason: str = "manual_edit",
    session: Optional[Session] = None,
) -> Optional[int]:
    """Capture the current on-disk graph state as a new snapshot row.

    Used by writers that don't route through `apply_diff` — e.g.
    `scan` refreshing refs on approved topics.

    No-op when the repo has no Repo row yet or no prior snapshot —
    those repos are still on the disk-only model and don't need
    snapshot updates until they see their first apply.

    Returns the new snapshot id, or None when skipped.
    """
    p = str(Path(repo_path).resolve())
    if not graph_exists(repo_path):
        return None

    def _run(s: Session) -> Optional[int]:
        repo = s.exec(select(Repo).where(Repo.path == p)).first()
        if repo is None or repo.id is None:
            return None  # repo not registered; disk-only mode
        prior = s.exec(
            select(GraphSnapshot)
            .where(GraphSnapshot.repo_id == repo.id)
            .where(GraphSnapshot.is_latest == 1)
        ).first()
        if prior is None:
            # No prior snapshot — this repo hasn't opted into the new
            # source-of-truth yet. Skip silently so legacy flows keep
            # working without surprise snapshots appearing.
            return None
        try:
            graph = _load_merged_disk(repo_path)
        except (OSError, json.JSONDecodeError, TopicGraphError):
            return None
        from datetime import datetime, timezone

        wiki_pages = _read_wiki_pages_from_disk(repo_path, graph)
        # Demote ALL is_latest rows, not just the `prior` found above — a
        # transient second latest row (from rapid cross-session writes)
        # would otherwise survive the single-row demote and collide with
        # this new is_latest=1 INSERT against the partial unique index.
        s.execute(
            sa_update(GraphSnapshot)
            .where(GraphSnapshot.repo_id == repo.id)
            .where(GraphSnapshot.is_latest == 1)
            .values(is_latest=0)
        )
        s.flush()
        snap = GraphSnapshot(
            repo_id=repo.id,
            taken_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            reason=reason,
            triggering_run_id=None,
            triggering_proposal_topic_id=None,
            graph_json=json.dumps(graph),
            wiki_pages_json=json.dumps(wiki_pages),
            diff_summary_json=json.dumps({"reason": reason, "via": "sync_snapshot_from_disk"}),
            pinned=0,
            is_latest=1,
        )
        s.add(snap)
        s.flush()
        return snap.id

    if session is not None:
        return _run(session)
    with SessionLocal() as s:
        snap_id = _run(s)
        s.commit()
        return snap_id


def check_graph_sync(repo_path: str | Path) -> dict:
    """Return sync state between the on-disk graph (+ wiki bodies)
    and the latest `is_latest=1` GraphSnapshot row.

    Surfaced by `regin doctor` so multi-user users can see whether
    their local cache reflects what a teammate just pushed via git.

    States:
    - `unregistered` — no Repo row for this path
    - `legacy_unsupported` — only a retired single-file `topic.json`
      is on disk; regin no longer reads it
    - `no_disk_file` — Repo registered, no split `topics/` dir with files
    - `no_snapshot` — Repo registered, disk present, no snapshot yet
      (first read will auto-seed; running `regin topics import` makes
      it explicit and tagged with `reason=manual`/`git_pull`)
    - `disk_unreadable` — the graph exists but can't be parsed
    - `disk_newer` — disk content (graph or wikis) differs from the
      latest snapshot; `regin topics import` reconciles
    - `in_sync` — disk and snapshot agree; nothing to do
    """
    from sqlmodel import select

    p = str(Path(repo_path).resolve())

    with SessionLocal() as s:
        repo = s.exec(select(Repo).where(Repo.path == p)).first()
        if repo is None or repo.id is None:
            return {"state": "unregistered"}
        if not graph_exists(repo_path):
            if topic_path(repo_path).exists():
                return {"state": "legacy_unsupported", "repo_id": repo.id}
            return {"state": "no_disk_file", "repo_id": repo.id}
        snap = s.exec(
            select(GraphSnapshot)
            .where(GraphSnapshot.repo_id == repo.id)
            .where(GraphSnapshot.is_latest == 1)
        ).first()

    if snap is None:
        return {"state": "no_snapshot", "repo_id": repo.id}

    try:
        disk_graph = _load_merged_disk(repo_path)
    except (OSError, json.JSONDecodeError, TopicGraphError) as exc:
        return {"state": "disk_unreadable", "repo_id": repo.id, "error": str(exc)}

    snap_graph = json.loads(snap.graph_json)
    disk_wikis = _read_wiki_pages_from_disk(repo_path, disk_graph)
    snap_wikis = json.loads(snap.wiki_pages_json or "{}")
    if _graph_hash(disk_graph) == _graph_hash(snap_graph) and disk_wikis == snap_wikis:
        return {"state": "in_sync", "repo_id": repo.id, "snapshot_id": snap.id}
    return {"state": "disk_newer", "repo_id": repo.id, "snapshot_id": snap.id}


def _disk_topic_count(repo_path: str | Path) -> int:
    try:
        return len(_load_merged_disk(repo_path).get("topics") or {})
    except (OSError, json.JSONDecodeError, TopicGraphError):
        return 0


def import_from_disk(repo_path: str | Path, *, reason: str = "manual") -> dict[str, Any]:
    """Sync the on-disk graph (+ wikis) into a snapshot — the shared core
    of `regin topics import` and the WebUI Import button.

    Bridges a teammate's git-shipped split dir + `wiki/*.md` into the
    local snapshot DB so they become routable/viewable. Idempotent: a
    no-op when already in sync. Returns a status dict the UI can render.
    """
    sync = check_graph_sync(repo_path)
    st = sync.get("state")
    base = {
        "state": st,
        "topic_count": _disk_topic_count(repo_path),
        "snapshot_id": sync.get("snapshot_id"),
    }
    if st in ("unregistered", "no_disk_file", "disk_unreadable", "in_sync"):
        return base
    if st == "no_snapshot":
        # First import on this machine — auto-seed the snapshot from disk.
        load_authoritative_graph(repo_path)
        repo_id = sync.get("repo_id")
        snap = latest_snapshot(repo_id) if repo_id else None
        return {**base, "state": "seeded", "snapshot_id": snap.id if snap else None}
    # disk_newer — capture the divergent disk state as a new snapshot.
    return {**base, "state": "imported", "snapshot_id": sync_snapshot_from_disk(repo_path, reason=reason)}


__all__ = [
    "check_graph_sync",
    "import_from_disk",
    "load_graph_from_snapshot",
    "load_authoritative_graph",
    "export_graph_to_disk",
    "export_overlay_to_disk",
    "reconcile_if_drifted",
    "repo_path_for",
    "sync_snapshot_from_disk",
]
