"""Snapshot orchestration for the approved graph.

`GraphSnapshot` is the source of truth for the approved graph from
Phase D onwards; in Phase A it sits alongside the on-disk graph and
gets written by `apply_diff` for every accept/merge/replace. Until the
flip, callers still read the disk graph — these helpers are write-side
machinery and a forward-compatible read API.

Pruning: callers may opt to keep the last N snapshots plus all pinned
rows. Default `keep=50` matches the plan; tighten via the CLI when
the table gets unwieldy.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import desc
from sqlmodel import Session, select

from lib.orm import SessionLocal
from lib.orm.models import GraphSnapshot, Repo
from lib.activity_log import get_activity_logger as _get_activity_logger


def _topics_log():
    return _get_activity_logger("topics")


def latest_snapshot(repo_id: int, *, session: Optional[Session] = None) -> Optional[GraphSnapshot]:
    """Return the single is_latest=1 row for the repo, or None.

    Uses a passed-in `session` when given (lets `apply_diff` keep
    everything in one transaction); otherwise opens a short-lived one.
    """
    if session is not None:
        return session.exec(
            select(GraphSnapshot)
            .where(GraphSnapshot.repo_id == repo_id)
            .where(GraphSnapshot.is_latest == 1)
        ).first()
    with SessionLocal() as s:
        return s.exec(
            select(GraphSnapshot)
            .where(GraphSnapshot.repo_id == repo_id)
            .where(GraphSnapshot.is_latest == 1)
        ).first()


def list_snapshots(
    repo_id: int,
    *,
    limit: int = 50,
    include_unpinned: bool = True,
    session: Optional[Session] = None,
) -> list[GraphSnapshot]:
    """Newest first. Used by the `/snapshots` endpoint and CLI."""
    def _run(s: Session) -> list[GraphSnapshot]:
        stmt = (
            select(GraphSnapshot)
            .where(GraphSnapshot.repo_id == repo_id)
            .order_by(desc(GraphSnapshot.id))
            .limit(limit)
        )
        if not include_unpinned:
            stmt = stmt.where(GraphSnapshot.pinned == 1)
        return list(s.exec(stmt))

    if session is not None:
        return _run(session)
    with SessionLocal() as s:
        return _run(s)


def graph_from_snapshot(snapshot: GraphSnapshot) -> dict[str, Any]:
    """Decode the `graph_json` payload from a snapshot row."""
    return json.loads(snapshot.graph_json)


def wiki_pages_from_snapshot(snapshot: GraphSnapshot) -> dict[str, str]:
    """Decode the `wiki_pages_json` payload.

    Phase A leaves this empty `{}` for every snapshot — the per-topic
    wiki split happens later. The shape is locked in now so apply.py
    and graph_io.py can already serialize against it.
    """
    return json.loads(snapshot.wiki_pages_json or "{}")


def prune_snapshots(
    repo_id: int,
    *,
    keep: int = 50,
    session: Optional[Session] = None,
) -> int:
    """Drop snapshots beyond the `keep` newest non-latest rows.

    Always preserves: (a) `is_latest=1`, (b) `pinned=1`. Returns the
    number of rows deleted. Safe to call repeatedly.
    """
    def _run(s: Session) -> int:
        # Find candidates: not latest, not pinned, ordered newest-first.
        # Anything past `keep` gets deleted.
        candidates = list(s.exec(
            select(GraphSnapshot)
            .where(GraphSnapshot.repo_id == repo_id)
            .where(GraphSnapshot.is_latest == 0)
            .where(GraphSnapshot.pinned == 0)
            .order_by(desc(GraphSnapshot.id))
        ))
        to_delete = candidates[keep:]
        for snap in to_delete:
            s.delete(snap)
        return len(to_delete)

    if session is not None:
        n = _run(session)
        session.flush()
        if n > 0:
            _topics_log().write("snapshots_pruned", repo_id=repo_id, keep=keep, deleted=n)
        return n
    with SessionLocal() as s:
        n = _run(s)
        s.commit()
    if n > 0:
        _topics_log().write("snapshots_pruned", repo_id=repo_id, keep=keep, deleted=n)
    return n


def restore_preview(
    snapshot_id: int,
    *,
    session: Optional[Session] = None,
) -> dict[str, Any]:
    """Compute what would change if `snapshot_id` were restored as latest.

    Restore goes FROM the current `is_latest=1` state TO the source
    snapshot's state. So per topic:
      - in current only → kind="would_remove" (disappears on restore)
      - in source only  → kind="would_add_back" (reappears on restore)
      - in both, differs → kind="would_revert" (contents revert to old)
      - in both, identical → not emitted

    Wiki diff is summarised by topic id only: a list of topic ids whose
    `wiki_pages_json` body differs between source and current. The full
    bodies aren't shipped — UI fetches per-topic wiki on demand.

    Caller can pre-flight a restore with this: the dict is JSON-safe and
    drives the "Preview restore" confirmation panel before the user
    commits to mutating the graph.
    """
    from lib.topics.diff import compute_topic_delta

    def _run(s: Session) -> dict[str, Any]:
        src = s.get(GraphSnapshot, snapshot_id)
        if src is None:
            raise ValueError(f"snapshot {snapshot_id} not found")
        latest = latest_snapshot(src.repo_id, session=s)

        src_graph = json.loads(src.graph_json or "{}")
        cur_graph = json.loads(latest.graph_json) if latest is not None else {}
        src_topics = src_graph.get("topics", {}) or {}
        cur_topics = cur_graph.get("topics", {}) or {}
        all_ids = set(src_topics.keys()) | set(cur_topics.keys())

        deltas: list[dict[str, Any]] = []
        for tid in sorted(all_ids):
            src_t = src_topics.get(tid)
            cur_t = cur_topics.get(tid)
            if src_t == cur_t:
                continue
            if cur_t is None:
                kind = "would_add_back"
            elif src_t is None:
                kind = "would_remove"
            else:
                kind = "would_revert"
            d = compute_topic_delta(
                topic_id_after=tid,
                kind=kind,
                before=cur_t,
                after=src_t,
            )
            deltas.append({
                "kind": d.kind,
                "topic_id": d.topic_id,
                "alias_adds": list(d.alias_adds),
                "alias_removes": list(d.alias_removes),
                "ref_adds": [list(r) for r in d.ref_adds],
                "ref_removes": [list(r) for r in d.ref_removes],
                "edge_adds": [list(e) for e in d.edge_adds],
                "edge_removes": [list(e) for e in d.edge_removes],
                "scalar_changes": [list(s) for s in d.scalar_changes],
            })

        src_wikis = json.loads(src.wiki_pages_json or "{}") or {}
        cur_wikis = json.loads(latest.wiki_pages_json or "{}") if latest is not None else {}
        cur_wikis = cur_wikis or {}
        wiki_changes = sorted({
            tid for tid in set(src_wikis) | set(cur_wikis)
            if src_wikis.get(tid) != cur_wikis.get(tid)
        })

        return {
            "snapshot_id": src.id,
            "latest_id": latest.id if latest is not None else None,
            "is_latest": bool(src.is_latest),
            "topic_deltas": deltas,
            "wiki_changes": wiki_changes,
            "no_change": not deltas and not wiki_changes,
        }

    if session is not None:
        return _run(session)
    with SessionLocal() as s:
        return _run(s)


def restore_snapshot(
    snapshot_id: int,
    *,
    session: Optional[Session] = None,
) -> GraphSnapshot:
    """Create a NEW snapshot row cloning the data of an older one.

    Restore is a write op (not a flip of `is_latest`) so the audit
    trail preserves the fact that a restore happened — the new row
    is tagged `reason='undo'` and carries the prior `is_latest=0`.
    The is_latest flip happens via the same UPDATE-then-INSERT pattern
    `apply_diff` uses (see `apply.py`).

    This helper composes the clone; the caller (apply.py or a future
    CLI) wraps it in the cross-store transaction.
    """
    def _run(s: Session) -> GraphSnapshot:
        src = s.get(GraphSnapshot, snapshot_id)
        if src is None:
            raise ValueError(f"snapshot {snapshot_id} not found")
        from datetime import datetime, timezone

        prior_latest = s.exec(
            select(GraphSnapshot)
            .where(GraphSnapshot.repo_id == src.repo_id)
            .where(GraphSnapshot.is_latest == 1)
        ).first()
        if prior_latest is not None:
            prior_latest.is_latest = 0
            s.add(prior_latest)
            s.flush()  # flush BEFORE the new is_latest=1 insert
                       # so the partial unique index doesn't fire.

        clone = GraphSnapshot(
            repo_id=src.repo_id,
            taken_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            reason="undo",
            triggering_run_id=None,
            triggering_proposal_topic_id=None,
            graph_json=src.graph_json,
            wiki_pages_json=src.wiki_pages_json,
            diff_summary_json=json.dumps({"restored_from": src.id}),
            pinned=0,
            is_latest=1,
        )
        s.add(clone)
        s.flush()
        return clone

    if session is not None:
        snap = _run(session)
        _topics_log().write(
            "snapshot_restored",
            source_snapshot_id=snapshot_id, new_snapshot_id=snap.id,
            repo_id=snap.repo_id,
        )
        return snap
    with SessionLocal() as s:
        snap = _run(s)
        s.commit()
    _topics_log().write(
        "snapshot_restored",
        source_snapshot_id=snapshot_id, new_snapshot_id=snap.id,
        repo_id=snap.repo_id,
    )
    return snap


def resolve_or_create_repo(repo_path: str, *, session: Optional[Session] = None) -> Repo:
    """Look up the `Repo` row for `repo_path`, creating one if missing.

    The Phase A8 shim calls `apply_diff(repo_id, ...)` from the old
    file-path-based API. Tests and ad-hoc callers may not have run
    `regin add-repo`, so the shim lazy-upserts here. Production callers
    that go through `add-repo` find the row already and pay no cost.

    Tightening: Phase D removes this helper from the apply path — by
    then the ORM is the source of truth and an unregistered repo has
    nowhere to write a snapshot anyway.
    """
    from pathlib import Path

    p = str(Path(repo_path).resolve())
    name = Path(p).name
    created: list[bool] = []

    def _run(s: Session) -> Repo:
        existing = s.exec(select(Repo).where(Repo.path == p)).first()
        if existing is not None:
            return existing
        # Same name + different path can happen across machines; suffix
        # with a counter so the unique-on-name constraint stays satisfied.
        base_name = name or "repo"
        candidate = base_name
        i = 1
        while s.exec(select(Repo).where(Repo.name == candidate)).first() is not None:
            i += 1
            candidate = f"{base_name}-{i}"
        repo = Repo(name=candidate, path=p)
        s.add(repo)
        s.flush()
        created.append(True)
        return repo

    if session is not None:
        repo = _run(session)
    else:
        with SessionLocal() as s:
            repo = _run(s)
            s.commit()
            s.refresh(repo)
    if created:
        _topics_log().write(
            "repo_registered_for_topics",
            repo_id=repo.id, repo_name=repo.name, repo_path=p,
        )
    return repo


__all__ = [
    "latest_snapshot",
    "list_snapshots",
    "graph_from_snapshot",
    "wiki_pages_from_snapshot",
    "prune_snapshots",
    "restore_preview",
    "restore_snapshot",
    "resolve_or_create_repo",
]
