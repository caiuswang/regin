"""ProposalFeedbackThread + ProposalFeedbackComment CRUD.

Feedback threads anchor inline review comments on proposal topics or
wiki ranges. The auto-resolve sweep (`orm_mark_feedback_threads_addressed`)
runs when a new revision lands and snaps a thread to `addressed` if
its anchored content actually changed between revisions.
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
    ProposalRun,
)

from ._common import _repo_for_path, _resolve_repo_for_write, _topics_log, _utc_now
from .revisions import _latest_revision, _revision_number_map
from .serializers import _feedback_thread_to_dict


def orm_list_feedback_threads(
    repo_path: str | Path,
    proposal_id: str,
    *,
    revision_id: int | None = None,
) -> list[dict[str, Any]]:
    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        if run is None:
            return []
        repo = _repo_for_path(s, repo_path)
        if repo is None or run.repo_id != repo.id:
            return []
        revision_numbers = _revision_number_map(s, proposal_id)
        selected_revision_number = _resolve_selected_revision_number(
            s, proposal_id, revision_id, revision_numbers,
        )
        threads = _fetch_threads_for_revision(
            s, proposal_id, selected_revision_number, revision_numbers,
        )
        if not threads:
            return []
        comments_by_thread = _fetch_comments_for_threads(s, threads)
        return [
            _feedback_thread_to_dict(
                thread,
                comments_by_thread.get(thread.id or -1, []),
                revision_numbers=revision_numbers,
                selected_revision_number=selected_revision_number,
            )
            for thread in threads
        ]


def _resolve_selected_revision_number(
    s: Session,
    proposal_id: str,
    revision_id: int | None,
    revision_numbers: dict[int, int],
) -> int | None:
    if revision_id is not None:
        selected = revision_numbers.get(revision_id)
        if selected is not None:
            return selected
    latest_revision = _latest_revision(s, proposal_id)
    return latest_revision.revision_number if latest_revision is not None else None


def _fetch_threads_for_revision(
    s: Session,
    proposal_id: str,
    selected_revision_number: int | None,
    revision_numbers: dict[int, int],
) -> list[ProposalFeedbackThread]:
    threads = list(s.exec(
        select(ProposalFeedbackThread)
        .where(ProposalFeedbackThread.run_id == proposal_id)
        .order_by(ProposalFeedbackThread.updated_at.desc(), ProposalFeedbackThread.id.desc())
    ))
    if selected_revision_number is None:
        return threads
    return [
        thread for thread in threads
        if (
            thread.revision_id is None
            or revision_numbers.get(thread.revision_id, selected_revision_number)
                <= selected_revision_number
        )
    ]


def _fetch_comments_for_threads(
    s: Session,
    threads: list[ProposalFeedbackThread],
) -> dict[int, list[ProposalFeedbackComment]]:
    thread_ids = [thread.id for thread in threads if thread.id is not None]
    if not thread_ids:
        return {}
    comments = list(s.exec(
        select(ProposalFeedbackComment)
        .where(ProposalFeedbackComment.feedback_thread_id.in_(thread_ids))
        .order_by(
            ProposalFeedbackComment.created_at.asc(),
            ProposalFeedbackComment.id.asc(),
        )
    ))
    grouped: dict[int, list[ProposalFeedbackComment]] = {}
    for comment in comments:
        grouped.setdefault(comment.feedback_thread_id, []).append(comment)
    return grouped


def orm_create_feedback_thread(
    repo_path: str | Path,
    proposal_id: str,
    *,
    proposal_topic_id: Optional[str],
    kind: str,
    anchor_kind: str,
    anchor: dict[str, Any] | None,
    quoted_text: Optional[str],
    body: str,
    created_by: str = "user",
    metadata: dict[str, Any] | None = None,
    comment_metadata: dict[str, Any] | None = None,
) -> Optional[dict[str, Any]]:
    repo = _resolve_repo_for_write(repo_path)
    now = _utc_now()
    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        if run is None or run.repo_id != repo.id:
            return None
        latest_revision = _latest_revision(s, proposal_id)
        thread = ProposalFeedbackThread(
            run_id=proposal_id,
            revision_id=latest_revision.id if latest_revision is not None else None,
            proposal_topic_id=proposal_topic_id,
            kind=kind,
            anchor_kind=anchor_kind,
            anchor_json=json.dumps(anchor or {}),
            quoted_text=quoted_text,
            resolution_state="open",
            created_by=created_by,
            created_at=now,
            updated_at=now,
            metadata_json=json.dumps(metadata or {}),
        )
        s.add(thread)
        s.flush()
        if thread.id is None:
            s.rollback()
            return None
        comment = ProposalFeedbackComment(
            feedback_thread_id=thread.id,
            author_kind=created_by,
            body=body,
            created_at=now,
            updated_at=now,
            metadata_json=json.dumps(comment_metadata or {}),
        )
        s.add(comment)
        s.commit()
        s.refresh(thread)
        s.refresh(comment)
        return _feedback_thread_to_dict(
            thread,
            [comment],
            revision_numbers=_revision_number_map(s, proposal_id),
        )


def orm_add_feedback_comment(
    repo_path: str | Path,
    proposal_id: str,
    feedback_thread_id: int,
    *,
    body: str,
    author_kind: str = "user",
    metadata: dict[str, Any] | None = None,
) -> Optional[dict[str, Any]]:
    now = _utc_now()
    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        repo = _repo_for_path(s, repo_path)
        if run is None or repo is None or run.repo_id != repo.id:
            return None
        thread = s.get(ProposalFeedbackThread, feedback_thread_id)
        if thread is None or thread.run_id != proposal_id:
            return None
        comment = ProposalFeedbackComment(
            feedback_thread_id=feedback_thread_id,
            author_kind=author_kind,
            body=body,
            created_at=now,
            updated_at=now,
            metadata_json=json.dumps(metadata or {}),
        )
        thread.updated_at = now
        s.add(thread)
        s.add(comment)
        s.commit()
        s.refresh(thread)
        comments = list(s.exec(
            select(ProposalFeedbackComment)
            .where(ProposalFeedbackComment.feedback_thread_id == feedback_thread_id)
            .order_by(
                ProposalFeedbackComment.created_at.asc(),
                ProposalFeedbackComment.id.asc(),
            )
        ))
        return _feedback_thread_to_dict(
            thread,
            comments,
            revision_numbers=_revision_number_map(s, proposal_id),
        )


def _ordered_thread_comments(
    s: Session, feedback_thread_id: int,
) -> list[ProposalFeedbackComment]:
    return list(s.exec(
        select(ProposalFeedbackComment)
        .where(ProposalFeedbackComment.feedback_thread_id == feedback_thread_id)
        .order_by(
            ProposalFeedbackComment.created_at.asc(),
            ProposalFeedbackComment.id.asc(),
        )
    ))


def orm_open_content_drift_threads(
    repo_path: str | Path,
    *,
    kind: str,
    proposal_id: Optional[str] = None,
    topic_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Open feedback threads of the given `kind` (e.g. the content-drift
    refresh note) across this repo's proposal runs.

    The producer calls it with `proposal_id` + `topic_id` to check whether
    an unresolved drift note already exists (idempotency); the agent-spawn
    consumer calls it unfiltered to find every origin run that still carries
    a pending drift refresh. Returns `[{run_id, topic_id, thread_id,
    drifted_paths}]` — `drifted_paths` is read back from the thread metadata.
    """
    with SessionLocal() as s:
        repo = _repo_for_path(s, repo_path)
        if repo is None:
            return []
        run_ids = {
            run.id for run in s.exec(
                select(ProposalRun).where(ProposalRun.repo_id == repo.id)
            )
        }
        if not run_ids:
            return []
        query = (
            select(ProposalFeedbackThread)
            .where(ProposalFeedbackThread.kind == kind)
            .where(ProposalFeedbackThread.resolution_state == "open")
            .where(ProposalFeedbackThread.run_id.in_(run_ids))
        )
        if proposal_id is not None:
            query = query.where(ProposalFeedbackThread.run_id == proposal_id)
        if topic_id is not None:
            query = query.where(ProposalFeedbackThread.proposal_topic_id == topic_id)
        out: list[dict[str, Any]] = []
        for thread in s.exec(query):
            try:
                meta = json.loads(thread.metadata_json or "{}")
            except json.JSONDecodeError:
                meta = {}
            out.append({
                "run_id": thread.run_id,
                "topic_id": thread.proposal_topic_id,
                "thread_id": thread.id,
                "drifted_paths": meta.get("drifted_paths") or [],
            })
        return out


# States a user may set by hand. "addressed" is reserved for the auto-resolve
# sweep that runs on regenerate, so it is intentionally excluded here.
MANUAL_RESOLUTION_STATES: frozenset[str] = frozenset({"open", "resolved", "dismissed"})


def orm_set_feedback_thread_resolution(
    repo_path: str | Path,
    proposal_id: str,
    feedback_thread_id: int,
    *,
    resolution_state: str,
) -> Optional[dict[str, Any]]:
    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        repo = _repo_for_path(s, repo_path)
        if run is None or repo is None or run.repo_id != repo.id:
            return None
        thread = s.get(ProposalFeedbackThread, feedback_thread_id)
        if thread is None or thread.run_id != proposal_id:
            return None
        thread.resolution_state = resolution_state
        # Reopening clears the auto-addressed pointer so the thread is treated
        # as live feedback again (and carried into the next regenerate).
        if resolution_state == "open":
            thread.addressed_in_revision_id = None
        # Deliberately leave updated_at untouched: closing a thread should not
        # resurface it to the top of the updated_at-sorted list.
        s.add(thread)
        s.commit()
        s.refresh(thread)
        return _feedback_thread_to_dict(
            thread,
            _ordered_thread_comments(s, feedback_thread_id),
            revision_numbers=_revision_number_map(s, proposal_id),
        )


def orm_update_feedback_comment(
    repo_path: str | Path,
    proposal_id: str,
    feedback_thread_id: int,
    comment_id: int,
    *,
    body: str,
) -> Optional[dict[str, Any]]:
    now = _utc_now()
    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        repo = _repo_for_path(s, repo_path)
        if run is None or repo is None or run.repo_id != repo.id:
            return None
        thread = s.get(ProposalFeedbackThread, feedback_thread_id)
        if thread is None or thread.run_id != proposal_id:
            return None
        comment = s.get(ProposalFeedbackComment, comment_id)
        if comment is None or comment.feedback_thread_id != feedback_thread_id:
            return None
        comment.body = body
        comment.updated_at = now
        s.add(comment)
        s.commit()
        s.refresh(thread)
        return _feedback_thread_to_dict(
            thread,
            _ordered_thread_comments(s, feedback_thread_id),
            revision_numbers=_revision_number_map(s, proposal_id),
        )


def orm_delete_feedback_comment(
    repo_path: str | Path,
    proposal_id: str,
    feedback_thread_id: int,
    comment_id: int,
) -> Optional[dict[str, Any]]:
    """Delete a comment. If it was the thread's last comment, the now-empty
    thread is deleted too. Returns the refreshed thread dict, or a
    ``{"deleted_thread": True}`` marker when the whole thread was removed.
    None signals the thread/comment was not found."""
    now = _utc_now()
    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        repo = _repo_for_path(s, repo_path)
        if run is None or repo is None or run.repo_id != repo.id:
            return None
        thread = s.get(ProposalFeedbackThread, feedback_thread_id)
        if thread is None or thread.run_id != proposal_id:
            return None
        comment = s.get(ProposalFeedbackComment, comment_id)
        if comment is None or comment.feedback_thread_id != feedback_thread_id:
            return None
        s.delete(comment)
        s.flush()
        remaining = _ordered_thread_comments(s, feedback_thread_id)
        if not remaining:
            s.delete(thread)
            s.commit()
            return {"deleted_thread": True, "feedback_thread_id": feedback_thread_id}
        thread.updated_at = now
        s.add(thread)
        s.commit()
        s.refresh(thread)
        return _feedback_thread_to_dict(
            thread,
            remaining,
            revision_numbers=_revision_number_map(s, proposal_id),
        )


_SNAPSHOT_LIST_FIELDS: tuple[str, ...] = (
    "aliases", "refs", "edges", "commands",
    "include_globs", "exclude_globs", "evidence_paths",
)


def _topic_full_snapshot(topic: dict[str, Any]) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "label": topic.get("label"),
        "intent": topic.get("intent"),
        "status": topic.get("status"),
    }
    for field in _SNAPSHOT_LIST_FIELDS:
        snap[field] = topic.get(field) or []
    return snap


def _topic_snapshot_value(topic: dict[str, Any] | None, field: str | None = None) -> Any:
    if topic is None:
        return None
    if not field:
        return _topic_full_snapshot(topic)
    value = topic.get(field)
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value


def _topics_by_id(proposal: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        topic.get("id"): topic
        for topic in proposal.get("topics", [])
        if isinstance(topic, dict) and topic.get("id")
    }


def _thread_addressed_by_revision(
    thread: ProposalFeedbackThread,
    previous_proposal: dict[str, Any],
    next_proposal: dict[str, Any],
) -> bool:
    previous_topics = _topics_by_id(previous_proposal)
    next_topics = _topics_by_id(next_proposal)
    previous_topic = previous_topics.get(thread.proposal_topic_id or "")
    next_topic = next_topics.get(thread.proposal_topic_id or "")
    anchor = json.loads(thread.anchor_json or "{}")

    if thread.anchor_kind == "topic_field":
        field = anchor.get("field")
        return _topic_snapshot_value(previous_topic, field) != _topic_snapshot_value(next_topic, field)
    if thread.anchor_kind == "proposal_summary":
        return _topic_snapshot_value(previous_topic) != _topic_snapshot_value(next_topic)
    if thread.anchor_kind == "wiki_range":
        return (previous_proposal.get("wiki") or "") != (next_proposal.get("wiki") or "")
    return False


def _is_thread_visible_at_revision(
    thread: ProposalFeedbackThread,
    revision_numbers: dict[int, int],
    from_revision_number: int | None,
) -> bool:
    """Skip threads from revisions newer than the one being addressed."""
    if from_revision_number is None or thread.revision_id is None:
        return True
    thread_revision_number = revision_numbers.get(thread.revision_id)
    if thread_revision_number is None:
        return True
    return thread_revision_number <= from_revision_number


def orm_mark_feedback_threads_addressed(
    repo_path: str | Path,
    proposal_id: str,
    *,
    from_revision_id: int,
    addressed_in_revision_id: int,
    previous_proposal: dict[str, Any],
    next_proposal: dict[str, Any],
) -> list[int]:
    if from_revision_id == addressed_in_revision_id:
        return []
    now = _utc_now()
    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        repo = _repo_for_path(s, repo_path)
        if run is None or repo is None or run.repo_id != repo.id:
            return []
        revision_numbers = _revision_number_map(s, proposal_id)
        from_revision_number = revision_numbers.get(from_revision_id)
        addressed_ids = _mark_open_threads_addressed(
            s, proposal_id, addressed_in_revision_id,
            from_revision_number, revision_numbers,
            previous_proposal, next_proposal, now,
        )
        if addressed_ids:
            s.commit()
    if addressed_ids:
        _topics_log().write(
            "proposal_feedback_threads_addressed",
            proposal_id=proposal_id,
            from_revision_id=from_revision_id,
            addressed_in_revision_id=addressed_in_revision_id,
            thread_count=len(addressed_ids),
            repo_path=str(repo_path),
        )
    return addressed_ids


def _mark_open_threads_addressed(
    s: Session,
    proposal_id: str,
    addressed_in_revision_id: int,
    from_revision_number: int | None,
    revision_numbers: dict[int, int],
    previous_proposal: dict[str, Any],
    next_proposal: dict[str, Any],
    now: str,
) -> list[int]:
    addressed_ids: list[int] = []
    threads = list(s.exec(
        select(ProposalFeedbackThread)
        .where(ProposalFeedbackThread.run_id == proposal_id)
        .where(ProposalFeedbackThread.resolution_state == "open")
    ))
    for thread in threads:
        if thread.id is None:
            continue
        if not _is_thread_visible_at_revision(thread, revision_numbers, from_revision_number):
            continue
        if not _thread_addressed_by_revision(thread, previous_proposal, next_proposal):
            continue
        thread.resolution_state = "addressed"
        thread.addressed_in_revision_id = addressed_in_revision_id
        thread.updated_at = now
        s.add(thread)
        addressed_ids.append(thread.id)
    return addressed_ids
