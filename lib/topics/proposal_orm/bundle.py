"""Portable-bundle assembly + ingest for one proposal run.

A bundle carries a run's full review state (run fields, revision chain
with topic snapshots, feedback threads + comments) in a machine-neutral
shape: SQLite PKs are per-machine, so revisions travel by
`revision_number`, threads reference revisions the same way, and every
FK is rebuilt locally at import time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, select

from lib.orm import SessionLocal
from lib.orm.models import (
    ProposalFeedbackComment,
    ProposalFeedbackThread,
    ProposalRevision,
    ProposalRevisionTopic,
    ProposalRun,
)

from ._common import _repo_for_path, _resolve_repo_for_write, _utc_now
from .feedback import _ordered_thread_comments
from .revisions import _revision_rows, _revision_topics
from .runs import (
    _cascade_delete_feedback,
    _cascade_delete_legacy_topics,
    _cascade_delete_revisions,
)
from .serializers import _proposed_topic_kwargs, _topic_to_dict


# ───────────────────────────── export ───────────────────────────────


def _run_to_bundle_dict(run: ProposalRun) -> dict[str, Any]:
    return {
        "provider": run.provider,
        "scope": run.scope,
        "state": run.state,
        "agent": run.agent_id,
        "complexity": run.complexity,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "updated_at": run.updated_at,
        "error": run.error,
        "error_detail": run.error_detail,
        "prompt_template_ids": json.loads(run.prompt_template_slugs or "[]"),
        "evidence_hash": run.evidence_hash,
        "regenerate_scope": run.regenerate_scope,
        "metadata": json.loads(run.metadata_json or "{}"),
        "topic_request": run.topic_request,
    }


def _revision_to_bundle_dict(
    s: Session,
    revision: ProposalRevision,
    number_by_id: dict[int, int],
) -> dict[str, Any]:
    return {
        "revision_number": revision.revision_number,
        "parent_revision_number": number_by_id.get(revision.parent_revision_id or -1),
        "kind": revision.kind,
        "wiki_md": revision.wiki_md or "",
        "is_latest": bool(revision.is_latest),
        "created_at": revision.created_at,
        "updated_at": revision.updated_at,
        "metadata": json.loads(revision.metadata_json or "{}"),
        "topics": [_topic_to_dict(t) for t in _revision_topics(s, revision.id or -1)],
    }


def _comment_to_bundle_dict(comment: ProposalFeedbackComment) -> dict[str, Any]:
    return {
        "author_kind": comment.author_kind,
        "body": comment.body,
        "created_at": comment.created_at,
        "updated_at": comment.updated_at,
        "metadata": json.loads(comment.metadata_json or "{}"),
    }


def _thread_to_bundle_dict(
    s: Session,
    thread: ProposalFeedbackThread,
    number_by_id: dict[int, int],
) -> dict[str, Any]:
    return {
        "revision_number": number_by_id.get(thread.revision_id or -1),
        "proposal_topic_id": thread.proposal_topic_id,
        "kind": thread.kind,
        "anchor_kind": thread.anchor_kind,
        "anchor": json.loads(thread.anchor_json or "{}"),
        "quoted_text": thread.quoted_text,
        "resolution_state": thread.resolution_state,
        "addressed_in_revision_number": number_by_id.get(
            thread.addressed_in_revision_id or -1),
        "created_by": thread.created_by,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
        "metadata": json.loads(thread.metadata_json or "{}"),
        "comments": [
            _comment_to_bundle_dict(c)
            for c in _ordered_thread_comments(s, thread.id or -1)
        ],
    }


def orm_export_proposal_bundle_parts(
    repo_path: str | Path, proposal_id: str,
) -> Optional[dict[str, Any]]:
    """Return the machine-neutral {run, revisions, feedback_threads}
    parts of a bundle, or None if the run is missing / cross-repo.

    Threads are ordered (created_at, id) ascending so re-inserting them
    in bundle order reproduces the reader's id-based tie-breaks on the
    importing machine.
    """
    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        repo = _repo_for_path(s, repo_path)
        if run is None or repo is None or run.repo_id != repo.id:
            return None
        revisions = sorted(
            _revision_rows(s, proposal_id), key=lambda r: r.revision_number)
        number_by_id = {
            r.id: r.revision_number for r in revisions if r.id is not None}
        threads = list(s.exec(
            select(ProposalFeedbackThread)
            .where(ProposalFeedbackThread.run_id == proposal_id)
            .order_by(
                ProposalFeedbackThread.created_at.asc(),
                ProposalFeedbackThread.id.asc(),
            )
        ))
        return {
            "run": _run_to_bundle_dict(run),
            "revisions": [
                _revision_to_bundle_dict(s, r, number_by_id) for r in revisions],
            "feedback_threads": [
                _thread_to_bundle_dict(s, t, number_by_id) for t in threads],
        }


# ───────────────────────────── import ───────────────────────────────


def _resolve_import_conflict(
    s: Session, repo_id: int, proposal_id: str, force: bool,
) -> tuple[Optional[str], str]:
    """Return (refusal_message, action). A same-id run under a different
    repo row is always refused — force must not reach across repos."""
    existing = s.get(ProposalRun, proposal_id)
    if existing is None:
        return None, "created"
    if existing.repo_id != repo_id:
        return (
            f"proposal run {proposal_id} already exists locally for a "
            "different repo; not touching it", "refused",
        )
    if not force:
        return (
            f"proposal run {proposal_id} already exists locally; "
            "import with force to replace it", "refused",
        )
    _cascade_delete_revisions(s, proposal_id)
    _cascade_delete_legacy_topics(s, proposal_id)
    _cascade_delete_feedback(s, proposal_id)
    s.delete(existing)
    s.flush()
    return None, "replaced"


def _insert_bundle_run(
    s: Session, repo_id: int, proposal_id: str, run_fields: dict[str, Any],
) -> None:
    s.add(ProposalRun(
        id=proposal_id,
        repo_id=repo_id,
        provider=run_fields.get("provider") or "unknown",
        scope=run_fields.get("scope") or "all",
        state=run_fields.get("state") or "completed",
        agent_id=run_fields.get("agent"),
        complexity=run_fields.get("complexity") or "standard",
        started_at=run_fields.get("started_at") or _utc_now(),
        completed_at=run_fields.get("completed_at"),
        updated_at=run_fields.get("updated_at") or _utc_now(),
        error=run_fields.get("error"),
        error_detail=run_fields.get("error_detail"),
        prompt_template_slugs=json.dumps(
            run_fields.get("prompt_template_ids") or []),
        evidence_hash=run_fields.get("evidence_hash"),
        regenerate_scope=run_fields.get("regenerate_scope"),
        metadata_json=json.dumps(run_fields.get("metadata") or {}),
        topic_request=run_fields.get("topic_request"),
    ))
    s.flush()


def _revisions_with_latest_marked(
    revisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bundle revisions ascending by number, with a fallback `is_latest`
    on the newest one when a hand-edited bundle flags none — readers
    treat is_latest=1 as the run's current content."""
    ordered = sorted(revisions, key=lambda r: r.get("revision_number") or 0)
    if ordered and not any(r.get("is_latest") for r in ordered):
        ordered = [dict(r) for r in ordered]
        ordered[-1]["is_latest"] = True
    return ordered


def _bundle_revision_row(
    proposal_id: str, r: dict[str, Any], id_by_number: dict[int, int],
) -> ProposalRevision:
    return ProposalRevision(
        run_id=proposal_id,
        revision_number=r.get("revision_number") or 1,
        parent_revision_id=_local_revision_id(
            id_by_number, r.get("parent_revision_number")),
        kind=r.get("kind") or "generated",
        wiki_md=r.get("wiki_md") or "",
        is_latest=1 if r.get("is_latest") else 0,
        created_at=r.get("created_at") or _utc_now(),
        updated_at=r.get("updated_at") or r.get("created_at") or _utc_now(),
        metadata_json=json.dumps(r.get("metadata") or {}),
    )


def _insert_bundle_revisions(
    s: Session, proposal_id: str, revisions: list[dict[str, Any]],
) -> dict[int, int]:
    """Insert revision rows + topic snapshots; returns the
    revision_number -> new local id map the thread import wires FKs from."""
    id_by_number: dict[int, int] = {}
    for r in _revisions_with_latest_marked(revisions):
        row = _bundle_revision_row(proposal_id, r, id_by_number)
        s.add(row)
        s.flush()
        if row.id is None:
            continue
        id_by_number[row.revision_number] = row.id
        for topic in r.get("topics") or []:
            if isinstance(topic, dict):
                s.add(ProposalRevisionTopic(
                    revision_id=row.id, **_proposed_topic_kwargs(topic)))
    return id_by_number


def _insert_bundle_thread_comments(
    s: Session, thread_id: int, comments: list[dict[str, Any]],
) -> None:
    for c in comments:
        if not isinstance(c, dict):
            continue
        s.add(ProposalFeedbackComment(
            feedback_thread_id=thread_id,
            author_kind=c.get("author_kind") or "user",
            body=c.get("body") or "",
            created_at=c.get("created_at") or _utc_now(),
            updated_at=c.get("updated_at") or c.get("created_at") or _utc_now(),
            metadata_json=json.dumps(c.get("metadata") or {}),
        ))


def _local_revision_id(
    id_by_number: dict[int, int], revision_number: Any,
) -> Optional[int]:
    """Rewire a bundle's revision_number reference to the local PK the
    revision insert just produced (None stays None — general threads)."""
    if not isinstance(revision_number, int):
        return None
    return id_by_number.get(revision_number)


def _bundle_thread_row(
    proposal_id: str, t: dict[str, Any], id_by_number: dict[int, int],
) -> ProposalFeedbackThread:
    return ProposalFeedbackThread(
        run_id=proposal_id,
        revision_id=_local_revision_id(id_by_number, t.get("revision_number")),
        proposal_topic_id=t.get("proposal_topic_id"),
        kind=t.get("kind") or "comment",
        anchor_kind=t.get("anchor_kind") or "general",
        anchor_json=json.dumps(t.get("anchor") or {}),
        quoted_text=t.get("quoted_text"),
        resolution_state=t.get("resolution_state") or "open",
        addressed_in_revision_id=_local_revision_id(
            id_by_number, t.get("addressed_in_revision_number")),
        created_by=t.get("created_by") or "user",
        created_at=t.get("created_at") or _utc_now(),
        updated_at=t.get("updated_at") or _utc_now(),
        metadata_json=json.dumps(t.get("metadata") or {}),
    )


def _insert_bundle_threads(
    s: Session,
    proposal_id: str,
    threads: list[dict[str, Any]],
    id_by_number: dict[int, int],
) -> int:
    count = 0
    for t in threads:
        if not isinstance(t, dict):
            continue
        thread = _bundle_thread_row(proposal_id, t, id_by_number)
        s.add(thread)
        s.flush()
        if thread.id is not None:
            _insert_bundle_thread_comments(s, thread.id, t.get("comments") or [])
        count += 1
    return count


def orm_import_proposal_bundle(
    repo_path: str | Path,
    bundle: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Recreate a bundled run + revisions + feedback threads in the
    local ORM. Review-state seeding only: never touches the approved
    graph, emits no notifications, marks nothing applied.

    The repo row is lazy-upserted (resolve_or_create semantics), so a
    bundle imports cleanly on a machine that never ran `add-repo`.
    """
    proposal_id = str(bundle.get("proposal_id") or "")
    repo = _resolve_repo_for_write(repo_path)
    with SessionLocal() as s:
        refusal, action = _resolve_import_conflict(s, repo.id, proposal_id, force)
        if refusal is not None:
            return {
                "proposal_id": proposal_id, "revisions": 0, "threads": 0,
                "action": "refused", "message": refusal,
            }
        _insert_bundle_run(s, repo.id, proposal_id, bundle.get("run") or {})
        id_by_number = _insert_bundle_revisions(
            s, proposal_id, bundle.get("revisions") or [])
        thread_count = _insert_bundle_threads(
            s, proposal_id, bundle.get("feedback_threads") or [], id_by_number)
        s.commit()
    return {
        "proposal_id": proposal_id,
        "revisions": len(id_by_number),
        "threads": thread_count,
        "action": action,
    }
