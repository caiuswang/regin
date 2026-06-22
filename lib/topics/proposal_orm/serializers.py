"""ORM row → dict (and back) converters for ProposalRun / Topic / Revision / Feedback.

The disk shape these emit/consume is the legacy `topics.json` /
`status.json` layout. Phase E2 made the ORM authoritative; these
converters keep the disk-shape contract intact for every caller that
still reads/writes JSON via load_proposal / save_proposal.
"""

from __future__ import annotations

import json
from typing import Any

from lib.orm.models import (
    ProposalFeedbackComment,
    ProposalFeedbackThread,
    ProposalRevision,
    ProposalRevisionTopic,
    ProposalRun,
    ProposalTopic,
)


# Read-time-derived revision pointers. The proposal-load path
# (`_enrich_with_revision`) computes these from the *live* latest revision;
# they must never be persisted into `run.metadata_json`, where they'd go
# stale the moment a new revision is appended (regenerate / downgrade /
# restore). Stripped both on write (`_build_run_metadata`) and on read
# (`_run_to_status_dict`) so older polluted rows can't surface a stale
# pointer either.
DERIVED_REVISION_KEYS: tuple[str, ...] = (
    "latest_revision_id",
    "latest_revision_number",
    "latest_revision_kind",
)


def _run_to_status_dict(run: ProposalRun) -> dict[str, Any]:
    """Render a ProposalRun back as the legacy `status.json` shape.

    Spreads all of metadata_json's contents so callers that read
    arbitrary status keys (stdout_tail, exit_code, etc.) keep working —
    metadata is the catch-all for agent/process detail the ORM doesn't
    model as columns.
    """
    metadata = json.loads(run.metadata_json or "{}")
    for stale_key in DERIVED_REVISION_KEYS:
        metadata.pop(stale_key, None)
    # Normalize stuck-row states at read time so the frontend poller
    # doesn't ping forever on a row whose writer crashed mid-update.
    # The write-time invariant in orm_update_proposal_status catches new
    # rows; this fallback covers ones that were corrupted before the
    # invariant landed.
    effective_state = run.state
    if run.error and effective_state in {"queued", "running", "completed"}:
        effective_state = "failed"
    if run.completed_at and effective_state in {"queued", "running"}:
        effective_state = "completed"
    return {
        **metadata,
        "state": effective_state,
        "agent": run.agent_id,
        "error": run.error,
        "error_detail": run.error_detail,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "updated_at": run.updated_at,
        "prompt_template_ids": json.loads(run.prompt_template_slugs or "[]"),
        "topic_request": run.topic_request,
    }


_REVIEW_MARKER_FIELDS: tuple[tuple[str, str], ...] = (
    ("source", "source"),
    ("review_status", "review_status"),
    ("accepted_topic_id", "accepted_topic"),
    ("accepted_at", "accepted_at"),
    ("merged_topic_id", "merged_topic"),
    ("merged_at", "merged_at"),
    ("ignored_at", "ignored_at"),
    ("downgraded_from", "downgraded_from"),
    ("downgraded_at", "downgraded_at"),
)


def _apply_review_markers(
    out: dict[str, Any],
    topic: ProposalTopic | ProposalRevisionTopic,
) -> None:
    for attr, key in _REVIEW_MARKER_FIELDS:
        value = getattr(topic, attr)
        if value:
            out[key] = value
    if topic.replaced_existing:
        out["replaced_existing"] = True


def _topic_to_dict(topic: ProposalTopic | ProposalRevisionTopic) -> dict[str, Any]:
    """Render a proposal topic row back as one entry of `topics.json::topics[]`."""
    out: dict[str, Any] = {
        "id": topic.topic_id,
        "label": topic.label,
        "intent": topic.intent,
        "status": topic.status,
        "aliases": json.loads(topic.aliases_json or "[]"),
        "refs": json.loads(topic.refs_json or "[]"),
        "edges": json.loads(topic.edges_json or "[]"),
        "commands": json.loads(topic.commands_json or "[]"),
        "include_globs": json.loads(topic.include_globs_json or "[]"),
        "exclude_globs": json.loads(topic.exclude_globs_json or "[]"),
        "evidence_paths": json.loads(topic.evidence_paths_json or "[]"),
        "parent_id": topic.parent_id,
        "blurb": topic.blurb or "",
    }
    _apply_review_markers(out, topic)
    return out


def _revision_to_dict(revision: ProposalRevision) -> dict[str, Any]:
    return {
        "id": revision.id,
        "revision_number": revision.revision_number,
        "parent_revision_id": revision.parent_revision_id,
        "kind": revision.kind,
        "is_latest": bool(revision.is_latest),
        "created_at": revision.created_at,
        "updated_at": revision.updated_at,
        "metadata": json.loads(revision.metadata_json or "{}"),
    }


def _base_proposal_dict(
    run: ProposalRun,
    metadata: dict[str, Any],
    topics: list[ProposalTopic | ProposalRevisionTopic],
) -> dict[str, Any]:
    return {
        "version": 1,
        "repo": metadata.get("repo_name") or "",
        "provider": run.provider,
        "scope": run.scope,
        "status": metadata.get("proposal_status", "draft"),
        "metadata": metadata,
        "topics": [_topic_to_dict(t) for t in topics],
    }


def _enrich_with_revision(
    proposal: dict[str, Any],
    revision: ProposalRevision,
    metadata: dict[str, Any],
) -> None:
    proposal["revision"] = _revision_to_dict(revision)
    proposal["generated_at"] = revision.updated_at or revision.created_at
    if revision.wiki_md:
        proposal["wiki"] = revision.wiki_md
    proposal["metadata"] = {
        **metadata,
        "latest_revision_id": revision.id,
        "latest_revision_number": revision.revision_number,
        "latest_revision_kind": revision.kind,
    }


def _apply_run_optional_fields(
    proposal: dict[str, Any],
    run: ProposalRun,
    metadata: dict[str, Any],
) -> None:
    notes = metadata.get("notes")
    if notes:
        proposal["notes"] = notes
    if "generated_at" not in proposal and run.completed_at:
        proposal["generated_at"] = run.completed_at
    if run.topic_request:
        proposal["topic_request"] = run.topic_request


def _run_to_proposal_dict(
    run: ProposalRun,
    topics: list[ProposalTopic | ProposalRevisionTopic],
    *,
    revision: ProposalRevision | None = None,
    revisions: list[ProposalRevision] | None = None,
) -> dict[str, Any]:
    """Render a ProposalRun + latest revision/topics as the legacy proposal shape."""
    metadata = json.loads(run.metadata_json or "{}")
    proposal = _base_proposal_dict(run, metadata, topics)
    if revision is not None:
        _enrich_with_revision(proposal, revision, metadata)
    if revisions:
        proposal["revisions"] = [_revision_to_dict(r) for r in revisions]
    _apply_run_optional_fields(proposal, run, metadata)
    return proposal


_TOPIC_JSON_LIST_FIELDS: tuple[str, ...] = (
    "aliases", "refs", "edges", "commands",
    "include_globs", "exclude_globs", "evidence_paths",
)


def _proposed_topic_kwargs(topic: dict[str, Any]) -> dict[str, Any]:
    """Translate a legacy topic dict into kwargs for ProposalTopic /
    ProposalRevisionTopic construction."""
    kwargs: dict[str, Any] = {
        "topic_id": topic.get("id") or "",
        "label": topic.get("label") or topic.get("id") or "",
        "intent": topic.get("intent") or "",
        "status": topic.get("status") or "active",
        "source": topic.get("source"),
        "review_status": topic.get("review_status"),
        "accepted_topic_id": topic.get("accepted_topic"),
        "accepted_at": topic.get("accepted_at"),
        "merged_topic_id": topic.get("merged_topic"),
        "merged_at": topic.get("merged_at"),
        "ignored_at": topic.get("ignored_at"),
        "downgraded_from": topic.get("downgraded_from"),
        "downgraded_at": topic.get("downgraded_at"),
        "replaced_existing": 1 if topic.get("replaced_existing") else 0,
        "parent_id": topic.get("parent_id"),
        "blurb": topic.get("blurb") or "",
    }
    for field in _TOPIC_JSON_LIST_FIELDS:
        kwargs[f"{field}_json"] = json.dumps(topic.get(field) or [])
    return kwargs


def _feedback_comment_to_dict(comment: ProposalFeedbackComment) -> dict[str, Any]:
    return {
        "id": comment.id,
        "author_kind": comment.author_kind,
        "body": comment.body,
        "created_at": comment.created_at,
        "updated_at": comment.updated_at,
        "metadata": json.loads(comment.metadata_json or "{}"),
    }


def _effective_resolution_state(
    thread: ProposalFeedbackThread,
    addressed_revision_number: int | None,
    selected_revision_number: int | None,
) -> str:
    """If the user is viewing an earlier revision than the one that
    closed this thread, surface it as "open" in the UI — the change
    that addressed it isn't visible from the selected revision yet."""
    if thread.resolution_state != "addressed":
        return thread.resolution_state
    if selected_revision_number is None or addressed_revision_number is None:
        return thread.resolution_state
    if addressed_revision_number > selected_revision_number:
        return "open"
    return thread.resolution_state


def _feedback_thread_to_dict(
    thread: ProposalFeedbackThread,
    comments: list[ProposalFeedbackComment],
    *,
    revision_numbers: dict[int, int] | None = None,
    selected_revision_number: int | None = None,
) -> dict[str, Any]:
    revision_numbers = revision_numbers or {}
    created_revision_number = revision_numbers.get(thread.revision_id or -1)
    addressed_revision_number = revision_numbers.get(thread.addressed_in_revision_id or -1)
    effective_resolution_state = _effective_resolution_state(
        thread, addressed_revision_number, selected_revision_number,
    )
    return {
        "id": thread.id,
        "revision_id": thread.revision_id,
        "revision_number": created_revision_number,
        "proposal_topic_id": thread.proposal_topic_id,
        "kind": thread.kind,
        "anchor_kind": thread.anchor_kind,
        "anchor": json.loads(thread.anchor_json or "{}"),
        "quoted_text": thread.quoted_text,
        "resolution_state": effective_resolution_state,
        "stored_resolution_state": thread.resolution_state,
        "addressed_in_revision_id": thread.addressed_in_revision_id,
        "addressed_in_revision_number": addressed_revision_number,
        "created_by": thread.created_by,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
        "metadata": json.loads(thread.metadata_json or "{}"),
        "comment_count": len(comments),
        "comments": [_feedback_comment_to_dict(comment) for comment in comments],
    }
