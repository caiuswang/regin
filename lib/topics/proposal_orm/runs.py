"""ProposalRun CRUD + the orm_save_proposal write path.

Public entry points:
  * orm_load_proposal_status / orm_load_proposal / orm_list_proposal_runs
  * orm_save_proposal — UPSERT a run + persist content to a revision
  * orm_create_proposal_run / orm_update_proposal_status
  * orm_unaccept_topic_across_proposals
  * orm_delete_proposal_run
  * orm_find_origin_proposal_run_for_topic
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, delete, select

from lib.orm import SessionLocal
from lib.orm.models import (
    ProposalFeedbackComment,
    ProposalFeedbackThread,
    ProposalRevision,
    ProposalRevisionTopic,
    ProposalRun,
    ProposalTopic,
    TopicAudit,
)
from lib.topics.core import topic_dir

from ._common import _repo_for_path, _resolve_repo_for_write, _topics_log, _utc_now
from .revisions import (
    _ensure_legacy_revision,
    _latest_revision,
    _purge_legacy_proposal_topics,
    _revision_rows,
    _revision_topics,
)
from .serializers import (
    DERIVED_REVISION_KEYS,
    _proposed_topic_kwargs,
    _run_to_proposal_dict,
    _run_to_status_dict,
)


# ─────────────────────────── read endpoints ─────────────────────────


def orm_load_proposal_status(repo_path: str | Path, proposal_id: str) -> Optional[dict[str, Any]]:
    """Return the `status.json` shape from ORM, or None if missing."""
    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        if run is None:
            return None
        repo = _repo_for_path(s, repo_path)
        if repo is None or run.repo_id != repo.id:
            return None
        return _run_to_status_dict(run)


def orm_load_proposal(repo_path: str | Path, proposal_id: str) -> Optional[dict[str, Any]]:
    """Return the `topics.json` shape from ORM, or None if no row exists."""
    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        if run is None:
            return None
        repo = _repo_for_path(s, repo_path)
        if repo is None or run.repo_id != repo.id:
            return None
        revision = _ensure_legacy_revision(s, repo_path, run)
        if revision is not None:
            topics = _revision_topics(s, revision.id or -1)
            revisions = _revision_rows(s, proposal_id)
            return _run_to_proposal_dict(run, topics, revision=revision, revisions=revisions)
        topics = list(s.exec(select(ProposalTopic).where(ProposalTopic.run_id == proposal_id)))
        return _run_to_proposal_dict(run, topics)


def _list_run_summary(
    s: Session,
    repo_path: str | Path,
    run: ProposalRun,
) -> dict[str, Any]:
    status = _run_to_status_dict(run)
    latest_revision = _ensure_legacy_revision(s, repo_path, run)
    if latest_revision is not None:
        topic_count = _revision_topics(s, latest_revision.id or -1)
        revision_count = len(_revision_rows(s, run.id))
        latest_revision_number = latest_revision.revision_number
        # When the run last changed: the latest revision's creation time
        # covers generate / regenerate / downgrade / restore. The run id is
        # a creation-time stamp that never moves, so a regenerated run would
        # otherwise read as old; this is the authoritative freshness signal.
        last_activity_at = latest_revision.created_at or run.completed_at or run.started_at
    else:
        topic_count = s.exec(
            select(ProposalTopic).where(ProposalTopic.run_id == run.id)
        ).all()
        revision_count = 0
        latest_revision_number = None
        last_activity_at = run.completed_at or run.started_at
    return {
        "id": run.id,
        # Use the normalized state from `status` so stuck rows
        # (state=running with completed_at set) don't pin the frontend
        # poller to "active" forever.
        "state": status["state"],
        "agent": run.agent_id,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "has_topics": bool(topic_count),
        "has_wiki": (topic_dir(repo_path) / "proposals" / run.id / "wiki.md").exists(),
        "path": str(topic_dir(repo_path) / "proposals" / run.id),
        "status": status,
        "revision_count": revision_count,
        "latest_revision_number": latest_revision_number,
        "last_activity_at": last_activity_at,
    }


def orm_list_proposal_runs(repo_path: str | Path) -> list[dict[str, Any]]:
    """List runs for a repo, newest first. Returns disk-compatible shape."""
    with SessionLocal() as s:
        repo = _repo_for_path(s, repo_path)
        if repo is None:
            return []
        runs = list(s.exec(
            select(ProposalRun)
            .where(ProposalRun.repo_id == repo.id)
            .order_by(ProposalRun.id.desc())
        ))
        return [_list_run_summary(s, repo_path, run) for run in runs]


# ─────────────────────────── orm_save_proposal ──────────────────────


def _build_run_metadata(proposal: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(proposal.get("metadata") or {})
    # The proposal dict we're handed often came from load_proposal, whose
    # serializer injects the live latest_revision_* pointers into metadata.
    # Persisting them here is what made run.metadata_json go stale; drop
    # them so the revision rows stay the single source of truth.
    for derived_key in DERIVED_REVISION_KEYS:
        metadata.pop(derived_key, None)
    if "repo" in proposal:
        metadata.setdefault("repo_name", proposal["repo"])
    if "status" in proposal:
        metadata["proposal_status"] = proposal["status"]
    if "notes" in proposal:
        metadata["notes"] = proposal["notes"]
    return metadata


def _upsert_proposal_run(
    s: Session,
    repo_id: int,
    proposal_id: str,
    proposal: dict[str, Any],
    metadata: dict[str, Any],
) -> ProposalRun:
    run = s.get(ProposalRun, proposal_id)
    if run is None:
        return _insert_new_run(s, repo_id, proposal_id, proposal, metadata)
    return _patch_existing_run(s, run, proposal, metadata)


def _insert_new_run(
    s: Session,
    repo_id: int,
    proposal_id: str,
    proposal: dict[str, Any],
    metadata: dict[str, Any],
) -> ProposalRun:
    generated_at = proposal.get("generated_at") or _utc_now()
    run = ProposalRun(
        id=proposal_id,
        repo_id=repo_id,
        provider=proposal.get("provider") or "unknown",
        scope=proposal.get("scope", "all"),
        state="completed",
        started_at=generated_at,
        completed_at=generated_at,
        updated_at=_utc_now(),
        prompt_template_slugs=json.dumps(metadata.get("prompt_template_ids") or []),
        metadata_json=json.dumps(metadata),
        topic_request=proposal.get("topic_request") or metadata.get("topic_request"),
    )
    s.add(run)
    return run


def _patch_existing_run(
    s: Session,
    run: ProposalRun,
    proposal: dict[str, Any],
    metadata: dict[str, Any],
) -> ProposalRun:
    existing_metadata = json.loads(run.metadata_json or "{}")
    existing_metadata.update(metadata)
    run.metadata_json = json.dumps(existing_metadata)
    run.updated_at = _utc_now()
    if proposal.get("topic_request"):
        run.topic_request = proposal["topic_request"]
    if proposal.get("provider"):
        run.provider = proposal["provider"]
    s.add(run)
    return run


def _revision_metadata_for(proposal: dict[str, Any], run: ProposalRun) -> dict[str, Any]:
    return {
        "provider": proposal.get("provider") or run.provider,
        "scope": proposal.get("scope", run.scope),
        "status": proposal.get("status"),
    }


def _create_initial_revision(
    s: Session,
    proposal_id: str,
    proposal: dict[str, Any],
    wiki: str | None,
    revision_kind: str | None,
    revision_metadata: dict[str, Any],
    now: str,
) -> ProposalRevision:
    generated_at = proposal.get("generated_at") or now
    revision = ProposalRevision(
        run_id=proposal_id,
        revision_number=1,
        parent_revision_id=None,
        kind=revision_kind or "generated",
        wiki_md=wiki or proposal.get("wiki") or "",
        is_latest=1,
        created_at=generated_at,
        updated_at=generated_at,
        metadata_json=json.dumps(revision_metadata),
    )
    s.add(revision)
    s.flush()
    return revision


def _append_new_revision(
    s: Session,
    proposal_id: str,
    proposal: dict[str, Any],
    latest: ProposalRevision,
    wiki: str | None,
    revision_kind: str | None,
    revision_metadata: dict[str, Any],
    now: str,
) -> ProposalRevision:
    latest.is_latest = 0
    latest.updated_at = now
    s.add(latest)
    s.flush()
    generated_at = proposal.get("generated_at") or now
    new_revision = ProposalRevision(
        run_id=proposal_id,
        revision_number=(latest.revision_number or 0) + 1,
        parent_revision_id=latest.id,
        kind=revision_kind or "generated",
        wiki_md=wiki or proposal.get("wiki") or "",
        is_latest=1,
        created_at=generated_at,
        updated_at=generated_at,
        metadata_json=json.dumps(revision_metadata),
    )
    s.add(new_revision)
    s.flush()
    return new_revision


def _update_existing_revision_in_place(
    s: Session,
    latest: ProposalRevision,
    wiki: str | None,
    revision_kind: str | None,
    revision_metadata: dict[str, Any],
    now: str,
) -> None:
    """Updating in place: leave `kind` alone unless the caller explicitly
    passed one. Previously the default of "generated" silently rewrote
    any prior kind (e.g. "restored") whenever a downstream save_proposal
    call landed (mark ready, accept, etc.), erasing the revision's
    origin in the UI."""
    if revision_kind is not None:
        latest.kind = revision_kind
    if wiki is not None:
        latest.wiki_md = wiki
    latest.updated_at = now
    latest.metadata_json = json.dumps({
        **json.loads(latest.metadata_json or "{}"),
        **revision_metadata,
    })
    s.add(latest)
    s.flush()


def _replace_revision_topics(session: Session, revision_id: int, topics: list[dict[str, Any]]) -> None:
    existing = list(session.exec(
        select(ProposalRevisionTopic).where(ProposalRevisionTopic.revision_id == revision_id)
    ))
    for old in existing:
        session.delete(old)
    session.flush()
    for topic in topics or []:
        if not isinstance(topic, dict):
            continue
        session.add(ProposalRevisionTopic(revision_id=revision_id, **_proposed_topic_kwargs(topic)))


def _persist_revision(
    s: Session,
    proposal_id: str,
    proposal: dict[str, Any],
    run: ProposalRun,
    *,
    wiki: str | None,
    append_revision: bool,
    revision_kind: str | None,
) -> ProposalRevision:
    now = _utc_now()
    revision_metadata = _revision_metadata_for(proposal, run)
    latest_revision = _latest_revision(s, proposal_id)
    if latest_revision is None:
        return _create_initial_revision(
            s, proposal_id, proposal, wiki, revision_kind, revision_metadata, now,
        )
    if append_revision:
        return _append_new_revision(
            s, proposal_id, proposal, latest_revision, wiki, revision_kind,
            revision_metadata, now,
        )
    _update_existing_revision_in_place(
        s, latest_revision, wiki, revision_kind, revision_metadata, now,
    )
    return latest_revision


def orm_save_proposal(
    repo_path: str | Path,
    proposal_id: str,
    proposal: dict[str, Any],
    *,
    wiki: str | None = None,
    append_revision: bool = False,
    revision_kind: str | None = None,
) -> None:
    """UPSERT a ProposalRun and persist proposal content to revisions.

    Caller responsibility: pre-fill `proposal["metadata"]` with everything
    the ORM can't model as a first-class column (agent_trace_id, pid,
    notes, etc.). This helper preserves any existing metadata that's not
    in the new payload. Proposal content lives in the latest revision;
    legacy `proposal_topics` rows are only the back-compat fallback for
    repos that predate revision persistence.
    """
    metadata = _build_run_metadata(proposal)
    repo = _resolve_repo_for_write(repo_path)
    with SessionLocal() as s:
        run = _upsert_proposal_run(s, repo.id, proposal_id, proposal, metadata)
        s.flush()
        latest_revision = _persist_revision(
            s, proposal_id, proposal, run,
            wiki=wiki, append_revision=append_revision, revision_kind=revision_kind,
        )
        if latest_revision.id is not None:
            _replace_revision_topics(s, latest_revision.id, proposal.get("topics") or [])
        # Replace legacy topics only until all consumers switch to revisions.
        _purge_legacy_proposal_topics(s, proposal_id)
        s.commit()


# ───────────────────────── status update / queue create ─────────────


def orm_create_proposal_run(
    repo_path: str | Path,
    proposal_id: str,
    *,
    provider: str,
    scope: str = "all",
    state: str = "queued",
    agent: Optional[str] = None,
    complexity: str = "standard",
    started_at: Optional[str] = None,
    prompt_template_ids: Optional[list[str]] = None,
    topic_request: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """INSERT a new ProposalRun row. Used by the run-create flows.

    No-op if a row with this id already exists (idempotent — the import
    script also writes here).
    """
    repo = _resolve_repo_for_write(repo_path)
    with SessionLocal() as s:
        existing = s.get(ProposalRun, proposal_id)
        if existing is not None:
            return
        s.add(ProposalRun(
            id=proposal_id,
            repo_id=repo.id,
            provider=provider,
            scope=scope,
            state=state,
            agent_id=agent,
            complexity=complexity,
            started_at=started_at or _utc_now(),
            updated_at=_utc_now(),
            prompt_template_slugs=json.dumps(prompt_template_ids or []),
            metadata_json=json.dumps(metadata or {}),
            topic_request=topic_request,
        ))
        s.commit()
    _topics_log().write(
        "proposal_run_created_in_orm",
        proposal_id=proposal_id, provider=provider, scope=scope,
        state=state, agent=agent, complexity=complexity,
        repo_path=str(repo_path),
    )


def _apply_status_invariants(run: ProposalRun) -> None:
    """A non-empty `error` always pins `state` to "failed". This keeps
    the row internally consistent when two writers race (success path
    setting state="completed" and fail path setting an error) —
    whichever order they land in, "error set" always means "not OK".

    completed_at set ⇒ the run actually finished; if state is still
    queued/running, a writer crashed mid-update and left the row
    inconsistent. The frontend poller treats those states as live and
    pings indefinitely — pin to "completed" so polling stops.
    """
    if run.error and run.state in {"queued", "running", "completed"}:
        run.state = "failed"
    if run.completed_at and run.state in {"queued", "running"}:
        run.state = "completed"


def _patch_status_fields(
    run: ProposalRun,
    *,
    state: Optional[str],
    completed_at: Optional[str],
    error: Optional[str],
    error_detail: Optional[str],
    clear_error: bool,
    clear_error_detail: bool,
    clear_completed_at: bool,
) -> None:
    if state is not None:
        run.state = state
    if completed_at is not None or clear_completed_at:
        run.completed_at = completed_at
    if error is not None or clear_error:
        run.error = error
    if error_detail is not None or clear_error_detail:
        run.error_detail = error_detail


def _merge_metadata_patch(run: ProposalRun, patch: Optional[dict[str, Any]]) -> None:
    if not patch:
        return
    existing = json.loads(run.metadata_json or "{}")
    existing.update(patch)
    run.metadata_json = json.dumps(existing)


def orm_update_proposal_status(
    repo_path: str | Path,
    proposal_id: str,
    *,
    state: Optional[str] = None,
    completed_at: Optional[str] = None,
    error: Optional[str] = None,
    error_detail: Optional[str] = None,
    metadata_patch: Optional[dict[str, Any]] = None,
    clear_error: bool = False,
    clear_error_detail: bool = False,
    clear_completed_at: bool = False,
) -> None:
    """Patch fields on a ProposalRun. No-op if the row is missing."""
    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        if run is None:
            return
        _patch_status_fields(
            run,
            state=state, completed_at=completed_at,
            error=error, error_detail=error_detail,
            clear_error=clear_error, clear_error_detail=clear_error_detail,
            clear_completed_at=clear_completed_at,
        )
        _apply_status_invariants(run)
        _merge_metadata_patch(run, metadata_patch)
        run.updated_at = _utc_now()
        s.add(run)
        s.commit()
        final_state = run.state
    _topics_log().write(
        "proposal_status_updated",
        proposal_id=proposal_id, state=final_state,
        completed_at=completed_at, error=error,
        repo_path=str(repo_path),
    )


# ─────────────────────────── unaccept sweep ─────────────────────────


def _clear_topic_accept_markers(t: ProposalTopic | ProposalRevisionTopic) -> None:
    t.review_status = None
    t.accepted_topic_id = None
    t.accepted_at = None


def orm_unaccept_topic_across_proposals(repo_path: str | Path, topic_id: str) -> int:
    """Clear the `accepted` review marker from every proposal row that
    previously accepted into `topic_id`.

    When the approved graph drops `topic_id` (downgrade, manual delete),
    the `review_status='accepted'` markers on the source proposals
    become stale claims — the UI's Accept button hides on accepted rows,
    so the user can't re-accept the same draft. This sweeps both
    `proposal_topics` (legacy) and `proposal_revision_topics` (current
    source of truth).

    The run-level `metadata.proposal_status` is left alone. The apply
    actually happened — there's a snapshot in `graph_snapshots` to prove
    it — so the proposal should keep showing up under the "Applied"
    filter. Topic-level `review_status` is what controls whether the
    Accept button reappears; the run-level status is history.

    Returns the number of rows reset.
    """
    reset = 0
    with SessionLocal() as s:
        repo = _repo_for_path(s, repo_path)
        if repo is None:
            return 0
        for run in s.exec(select(ProposalRun).where(ProposalRun.repo_id == repo.id)):
            reset += _reset_legacy_topics(s, run.id, topic_id)
            reset += _reset_revision_topics(s, run.id, topic_id)
        s.commit()
    return reset


def _reset_legacy_topics(s: Session, run_id: str, topic_id: str) -> int:
    legacy_topics = list(s.exec(
        select(ProposalTopic).where(
            ProposalTopic.run_id == run_id,
            ProposalTopic.accepted_topic_id == topic_id,
        )
    ))
    for t in legacy_topics:
        _clear_topic_accept_markers(t)
        s.add(t)
    return len(legacy_topics)


def _reset_revision_topics(s: Session, run_id: str, topic_id: str) -> int:
    revision_ids = [
        rev.id for rev in s.exec(
            select(ProposalRevision).where(ProposalRevision.run_id == run_id)
        ) if rev.id is not None
    ]
    if not revision_ids:
        return 0
    revision_topics = list(s.exec(
        select(ProposalRevisionTopic).where(
            ProposalRevisionTopic.revision_id.in_(revision_ids),
            ProposalRevisionTopic.accepted_topic_id == topic_id,
        )
    ))
    for t in revision_topics:
        _clear_topic_accept_markers(t)
        s.add(t)
    return len(revision_topics)


# ─────────────────────────── delete ─────────────────────────────────


def orm_delete_proposal_run(repo_path: str | Path, proposal_id: str) -> bool:
    """Delete a ProposalRun + cascading ProposalTopic, ProposalRevision*,
    and ProposalFeedback* rows."""
    with SessionLocal() as s:
        run = s.get(ProposalRun, proposal_id)
        if run is None:
            return False
        repo = _repo_for_path(s, repo_path)
        if repo is None or run.repo_id != repo.id:
            return False
        _cascade_delete_revisions(s, proposal_id)
        _cascade_delete_legacy_topics(s, proposal_id)
        _cascade_delete_feedback(s, proposal_id)
        s.delete(run)
        s.commit()
        return True


def _cascade_delete_revisions(s: Session, proposal_id: str) -> None:
    revision_ids = [
        revision.id for revision in s.exec(
            select(ProposalRevision).where(ProposalRevision.run_id == proposal_id)
        ) if revision.id is not None
    ]
    if revision_ids:
        s.exec(
            delete(ProposalRevisionTopic)
            .where(ProposalRevisionTopic.revision_id.in_(revision_ids))
        )
    for revision in s.exec(
        select(ProposalRevision).where(ProposalRevision.run_id == proposal_id)
    ):
        s.delete(revision)


def _cascade_delete_legacy_topics(s: Session, proposal_id: str) -> None:
    for topic in s.exec(
        select(ProposalTopic).where(ProposalTopic.run_id == proposal_id)
    ):
        s.delete(topic)


def _cascade_delete_feedback(s: Session, proposal_id: str) -> None:
    thread_ids = [
        thread.id for thread in s.exec(
            select(ProposalFeedbackThread).where(ProposalFeedbackThread.run_id == proposal_id)
        ) if thread.id is not None
    ]
    if thread_ids:
        s.exec(
            delete(ProposalFeedbackComment)
            .where(ProposalFeedbackComment.feedback_thread_id.in_(thread_ids))
        )
    for thread in s.exec(
        select(ProposalFeedbackThread).where(ProposalFeedbackThread.run_id == proposal_id)
    ):
        s.delete(thread)


# ─────────────────────────── provenance lookup ──────────────────────


def orm_find_origin_proposal_run_for_topic(
    repo_path: str | Path, topic_id: str,
) -> Optional[str]:
    """Return the proposal_run id that most recently brought `topic_id`
    into the approved graph for this repo, or None if no provenance row
    references it (legacy snapshots predate triggering_run_id) or the
    run has since been deleted.

    Reads append-only `topic_audits` rows with kind='provenance'. apply
    writes one row per affected topic, with `topic_ids_json='["<id>"]'`
    and `triggering_run_id` set to the proposal that drove the apply.
    """
    with SessionLocal() as s:
        repo = _repo_for_path(s, repo_path)
        if repo is None:
            return None
        rows = s.exec(
            select(TopicAudit)
            .where(TopicAudit.repo_id == repo.id)
            .where(TopicAudit.kind == "provenance")
            .order_by(TopicAudit.recorded_at.desc(), TopicAudit.id.desc())
        )
        for row in rows:
            if row.triggering_run_id is None:
                continue
            try:
                ids = json.loads(row.topic_ids_json or "[]")
            except json.JSONDecodeError:
                continue
            if topic_id not in ids:
                continue
            if s.get(ProposalRun, row.triggering_run_id) is not None:
                return row.triggering_run_id
    return None
