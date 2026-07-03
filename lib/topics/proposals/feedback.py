"""Proposal feedback thread + comment CRUD (thin wrappers over the ORM).

Each function validates its arguments, delegates the actual DB work to
the corresponding `orm_*` helper, then writes a topics activity-log
record so feedback activity is surfaced in `regin logs`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.topics import TopicGraphError

from ._common import _find_proposed_topic, _topics_log


def list_proposal_feedback_threads(
    repo_path: str | Path,
    proposal_id: str,
    *,
    revision_id: int | None = None,
) -> list[dict[str, Any]]:
    from lib.topics.proposal_orm import orm_list_feedback_threads

    return orm_list_feedback_threads(repo_path, proposal_id, revision_id=revision_id)


def _validate_feedback_thread_args(
    body: str, anchor: dict[str, Any] | None,
) -> str:
    if not body or not str(body).strip():
        raise TopicGraphError("body is required")
    if anchor is not None and not isinstance(anchor, dict):
        raise TopicGraphError("anchor must be an object")
    return str(body).strip()


def create_proposal_feedback_thread(
    repo_path: str | Path,
    proposal_id: str,
    *,
    proposal_topic_id: str | None,
    kind: str = "comment",
    anchor_kind: str = "general",
    anchor: dict[str, Any] | None = None,
    quoted_text: str | None = None,
    body: str,
    created_by: str = "user",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cleaned_body = _validate_feedback_thread_args(body, anchor)
    resolved_kind = kind or "comment"
    resolved_anchor_kind = anchor_kind or "general"
    resolved_created_by = created_by or "user"

    if proposal_topic_id:
        from .core_io import load_proposal
        proposal = load_proposal(repo_path, proposal_id)
        _find_proposed_topic(proposal, proposal_topic_id)

    from lib.topics.proposal_orm import orm_create_feedback_thread

    thread = orm_create_feedback_thread(
        repo_path,
        proposal_id,
        proposal_topic_id=proposal_topic_id,
        kind=resolved_kind,
        anchor_kind=resolved_anchor_kind,
        anchor=anchor,
        quoted_text=quoted_text,
        body=cleaned_body,
        created_by=resolved_created_by,
        metadata=metadata,
    )
    if thread is None:
        raise TopicGraphError(f"proposal run not found: {proposal_id}")
    _topics_log().write(
        "proposal_feedback_thread_created",
        proposal_id=proposal_id, proposal_topic_id=proposal_topic_id,
        kind=resolved_kind, anchor_kind=resolved_anchor_kind,
        created_by=resolved_created_by, repo_path=str(repo_path),
    )
    return thread


def add_proposal_feedback_comment(
    repo_path: str | Path,
    proposal_id: str,
    feedback_thread_id: int,
    *,
    body: str,
    author_kind: str = "user",
) -> dict[str, Any]:
    if not body or not str(body).strip():
        raise TopicGraphError("body is required")
    resolved_author = author_kind or "user"

    from lib.topics.proposal_orm import orm_add_feedback_comment

    thread = orm_add_feedback_comment(
        repo_path,
        proposal_id,
        feedback_thread_id,
        body=str(body).strip(),
        author_kind=resolved_author,
    )
    if thread is None:
        raise TopicGraphError(f"feedback thread not found: {feedback_thread_id}")
    _topics_log().write(
        "proposal_feedback_comment_added",
        proposal_id=proposal_id, feedback_thread_id=feedback_thread_id,
        author_kind=resolved_author, repo_path=str(repo_path),
    )
    return thread


def set_proposal_feedback_thread_resolution(
    repo_path: str | Path,
    proposal_id: str,
    feedback_thread_id: int,
    *,
    resolution_state: str,
) -> dict[str, Any]:
    from lib.topics.proposal_orm import (
        MANUAL_RESOLUTION_STATES,
        orm_set_feedback_thread_resolution,
    )

    state = (resolution_state or "").strip()
    if state not in MANUAL_RESOLUTION_STATES:
        raise TopicGraphError(
            f"resolution_state must be one of {sorted(MANUAL_RESOLUTION_STATES)}"
        )

    thread = orm_set_feedback_thread_resolution(
        repo_path,
        proposal_id,
        feedback_thread_id,
        resolution_state=state,
    )
    if thread is None:
        raise TopicGraphError(f"feedback thread not found: {feedback_thread_id}")
    _topics_log().write(
        "proposal_feedback_thread_resolution_set",
        proposal_id=proposal_id, feedback_thread_id=feedback_thread_id,
        resolution_state=state, repo_path=str(repo_path),
    )
    return thread


def dismiss_content_drift_thread(
    repo_path: str | Path,
    proposal_id: str,
    feedback_thread_id: int,
) -> dict[str, Any]:
    """Dismiss a content-drift note as *unrelated to the wiki* — and advance
    the topic's drift baseline so the note doesn't resurrect on the next
    evolve pass (see `lib.topics.content_drift.dismiss_content_drift`).

    Only valid on an *open* `content_drift` note: the eligible set comes from
    `orm_open_content_drift_threads` (open + kind-filtered), which also yields
    the topic to re-baseline. Anything else — a resolved note, a plain review
    comment, an unknown id — raises rather than silently re-fingerprinting the
    wrong topic."""
    from lib.topics.content_drift import (
        CONTENT_DRIFT_THREAD_KIND,
        dismiss_content_drift,
    )
    from lib.topics.proposal_orm import orm_open_content_drift_threads

    eligible = orm_open_content_drift_threads(
        repo_path, kind=CONTENT_DRIFT_THREAD_KIND, proposal_id=proposal_id)
    match = next(
        (t for t in eligible if t["thread_id"] == feedback_thread_id), None)
    if match is None:
        raise TopicGraphError(
            f"no open content-drift note {feedback_thread_id} on {proposal_id}")

    result = dismiss_content_drift(repo_path, match["topic_id"])
    _topics_log().write(
        "proposal_content_drift_dismissed",
        proposal_id=proposal_id, feedback_thread_id=feedback_thread_id,
        topic_id=match["topic_id"], digests_captured=result["digests_captured"],
        threads_dismissed=len(result["threads_dismissed"]),
        repo_path=str(repo_path),
    )
    return result


def update_proposal_feedback_comment(
    repo_path: str | Path,
    proposal_id: str,
    feedback_thread_id: int,
    comment_id: int,
    *,
    body: str,
) -> dict[str, Any]:
    if not body or not str(body).strip():
        raise TopicGraphError("body is required")

    from lib.topics.proposal_orm import orm_update_feedback_comment

    thread = orm_update_feedback_comment(
        repo_path,
        proposal_id,
        feedback_thread_id,
        comment_id,
        body=str(body).strip(),
    )
    if thread is None:
        raise TopicGraphError(f"feedback comment not found: {comment_id}")
    _topics_log().write(
        "proposal_feedback_comment_updated",
        proposal_id=proposal_id, feedback_thread_id=feedback_thread_id,
        comment_id=comment_id, repo_path=str(repo_path),
    )
    return thread


def delete_proposal_feedback_comment(
    repo_path: str | Path,
    proposal_id: str,
    feedback_thread_id: int,
    comment_id: int,
) -> dict[str, Any]:
    from lib.topics.proposal_orm import orm_delete_feedback_comment

    result = orm_delete_feedback_comment(
        repo_path,
        proposal_id,
        feedback_thread_id,
        comment_id,
    )
    if result is None:
        raise TopicGraphError(f"feedback comment not found: {comment_id}")
    _topics_log().write(
        "proposal_feedback_comment_deleted",
        proposal_id=proposal_id, feedback_thread_id=feedback_thread_id,
        comment_id=comment_id, deleted_thread=bool(result.get("deleted_thread")),
        repo_path=str(repo_path),
    )
    return result
