"""Background-thread proposal + regenerate runs for the external-agent path.

The external-agent provider spawns a subprocess. The web request that
kicks one off needs to return immediately, so `start_external_*` queues
status, spawns a daemon thread, and the thread runs `_draft_proposal` →
`_write_proposal_artifacts` → (optional) `orm_mark_feedback_threads_addressed`.

Any exception inside the thread is captured into the run's status file
so the failure surfaces in the UI rather than tearing down the worker.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.topics import TopicGraphError, topic_dir, utc_now
from lib.topics.proposal_drafting import _draft_proposal, _write_proposal_artifacts

from . import run_control
from ._common import (
    _guard_regenerate_not_in_flight,
    _reset_review_markers_for_regenerate,
    _resolve_prompt_templates,
)
from .core_io import load_proposal, load_proposal_status
from .feedback import list_proposal_feedback_threads


@dataclass
class _RegenerateInputs:
    """Resolved settings for one regenerate run."""

    prior_draft: dict[str, Any] | None
    previous_revision_id: int | None
    agent: str | None
    prompt_template_ids: list[str]
    topic_request: str | None


def _resolve_regenerate_inputs_from_proposal(
    repo: Path, proposal_id: str, prior_proposal: dict[str, Any], wiki_path: Path,
) -> _RegenerateInputs:
    feedback_threads = [
        thread
        for thread in list_proposal_feedback_threads(
            repo,
            proposal_id,
            revision_id=prior_proposal.get("revision", {}).get("id"),
        )
        if thread.get("resolution_state") == "open"
    ]
    metadata = prior_proposal.get("metadata") or {}
    return _RegenerateInputs(
        prior_draft={
            "proposal": prior_proposal,
            "wiki": wiki_path.read_text() if wiki_path.exists() else "",
            "feedback_threads": feedback_threads,
        },
        previous_revision_id=prior_proposal.get("revision", {}).get("id"),
        agent=metadata.get("agent"),
        prompt_template_ids=list(metadata.get("prompt_template_ids") or []),
        topic_request=prior_proposal.get("topic_request"),
    )


def _resolve_regenerate_inputs_from_status(
    repo: Path, proposal_id: str,
) -> _RegenerateInputs:
    try:
        status = load_proposal_status(repo, proposal_id)
    except TopicGraphError:
        raise TopicGraphError(
            f"proposal run has no draft or status to regenerate from: {proposal_id}"
        )
    return _RegenerateInputs(
        prior_draft=None,
        previous_revision_id=None,
        agent=status.get("agent"),
        prompt_template_ids=list(status.get("prompt_template_ids") or []),
        topic_request=status.get("topic_request"),
    )


def _resolve_regenerate_inputs(repo: Path, proposal_id: str) -> _RegenerateInputs:
    """ORM-backed lookup post-Phase-E: prefer the proposal dict if its
    topics list is present; otherwise fall back to the run's status row
    for the queued/failed external-agent path."""
    wiki_path = topic_dir(repo) / "proposals" / proposal_id / "wiki.md"
    try:
        prior_proposal = load_proposal(repo, proposal_id)
    except TopicGraphError:
        prior_proposal = None
    if prior_proposal and prior_proposal.get("topics"):
        return _resolve_regenerate_inputs_from_proposal(
            repo, proposal_id, prior_proposal, wiki_path,
        )
    return _resolve_regenerate_inputs_from_status(repo, proposal_id)


def _mark_addressed_feedback_after_regenerate(
    repo: Path,
    proposal_id: str,
    previous_revision_id: int | None,
    prior_proposal: dict[str, Any] | None,
    wiki: str,
) -> None:
    """Auto-resolve feedback threads whose anchored content changed
    between revisions. No-op when this was the first revision."""
    if previous_revision_id is None or prior_proposal is None:
        return
    latest_proposal = load_proposal(repo, proposal_id)
    latest_revision_id = latest_proposal.get("revision", {}).get("id")
    if latest_revision_id is None:
        return
    from lib.topics.proposal_orm import orm_mark_feedback_threads_addressed

    next_proposal = dict(latest_proposal)
    next_proposal["wiki"] = wiki.strip()
    orm_mark_feedback_threads_addressed(
        repo,
        proposal_id,
        from_revision_id=previous_revision_id,
        addressed_in_revision_id=latest_revision_id,
        previous_proposal=prior_proposal,
        next_proposal=next_proposal,
    )


# ───────────────────────── proposal start (async) ──────────────────────


def start_external_proposal_run(
    repo_path: str | Path,
    *,
    run_id: str | None = None,
    agent: str | None = None,
    topic_request: str | None = None,
    prompt_template_ids: list[str] | None = None,
) -> dict[str, Path]:
    """Start an external-agent proposal job in the background."""
    repo = Path(repo_path)
    proposal_id = run_id or utc_now().replace(":", "").replace("-", "")
    out_dir = topic_dir(repo) / "proposals" / proposal_id
    out_dir.mkdir(parents=True, exist_ok=True)

    from lib.topics.proposal_external import default_external_agent_id, external_trace_id, write_status

    resolved_ids = list(prompt_template_ids or [])
    write_status(out_dir, {
        "state": "queued",
        "trace_id": external_trace_id(proposal_id),
        "agent": agent or default_external_agent_id(),
        "started_at": None,
        "completed_at": None,
        "error": None,
        "pid": None,
        "prompt_template_ids": resolved_ids,
    })
    # Clear any stale cancel flag from a prior run with this id (the id is
    # reused across regenerate) so the fresh run isn't insta-cancelled.
    run_control.reset(proposal_id)
    threading.Thread(
        target=_external_proposal_job,
        kwargs={
            "repo": repo,
            "out_dir": out_dir,
            "proposal_id": proposal_id,
            "topic_request": topic_request,
            "agent": agent,
            "prompt_template_ids": resolved_ids,
        },
        daemon=True,
    ).start()
    return {
        "dir": out_dir,
        "topics": out_dir / "topics.json",
        "wiki": out_dir / "wiki.md",
    }


def _record_thread_failure(
    out_dir: Path, agent: str | None, exc: Exception,
) -> None:
    """Pin a fresh failure status when a background job crashes — the
    web worker is left alone so the dispatch pipeline keeps running."""
    from lib.topics.proposal_external import load_status, write_status

    status = load_status(out_dir) or {
        "state": "failed",
        "trace_id": None,
        "agent": agent,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "pid": None,
    }
    # A user-initiated stop is terminal — don't downgrade it to "failed"
    # when the killed subprocess surfaces as an exception in the worker.
    if status.get("state") == "cancelled":
        return
    if not status.get("error"):
        status["state"] = "failed"
        status["error"] = str(exc)
        status["completed_at"] = utc_now()
        write_status(out_dir, status)


def _mark_run_completed(out_dir: Path) -> None:
    """Stamp the run's terminal `completed` status on success.

    The real agent runner already marks completion; this also covers the
    fast paths (e.g. tests) that bypass the subprocess runner, so a queued
    run never gets stranded mid-flight.
    """
    from lib.topics.proposal_external import load_status, write_status

    status = load_status(out_dir) or {"state": "completed", "agent": None, "error": None}
    status["state"] = "completed"
    status["completed_at"] = utc_now()
    write_status(out_dir, status)


def _external_proposal_job(
    *,
    repo: Path,
    out_dir: Path,
    proposal_id: str,
    topic_request: str | None,
    agent: str | None,
    prompt_template_ids: list[str] | None = None,
) -> None:
    try:
        templates = _resolve_prompt_templates(prompt_template_ids)
        proposals, wiki = _draft_proposal(
            repo=repo,
            out_dir=out_dir,
            proposal_id=proposal_id,
            topic_request=topic_request,
            agent=agent,
            prompt_templates=templates,
        )
        proposals["status"] = "pending_review"
        _write_proposal_artifacts(
            out_dir, proposals=proposals, wiki=wiki,
            repo_path=repo, proposal_id=proposal_id,
            revision_kind="generated",
        )
        _mark_run_completed(out_dir)
        _maybe_review_note(repo, proposal_id)
    except Exception as exc:
        _record_thread_failure(out_dir, agent, exc)
    finally:
        run_control.release(proposal_id)


def _maybe_review_note(repo: Path, proposal_id: str) -> None:
    """Gated, best-effort LLM review note after a run completes. Imported
    lazily so the proposal_review → adapters chain isn't pulled in until a
    run finishes; the callee swallows its own errors."""
    from lib.topics.proposal_review import maybe_generate_review_note
    maybe_generate_review_note(repo, proposal_id)


# ───────────────────────── regenerate (async) ──────────────────────────


def start_external_regenerate_run(
    repo_path: str | Path,
    proposal_id: str,
) -> dict[str, Path]:
    """Start an external-agent regenerate job in the background."""
    repo = Path(repo_path)
    proposal_dir = topic_dir(repo) / "proposals" / proposal_id
    wiki_path = proposal_dir / "wiki.md"
    # Block concurrent regenerates racing on the same proposal_id.
    _guard_regenerate_not_in_flight(repo, proposal_id)

    inputs = _resolve_regenerate_inputs(repo, proposal_id)

    from lib.topics.proposal_external import external_trace_id, write_status

    write_status(proposal_dir, {
        "state": "queued",
        "trace_id": external_trace_id(proposal_id),
        "agent": inputs.agent,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "pid": None,
        "prompt_template_ids": inputs.prompt_template_ids,
    })
    run_control.reset(proposal_id)
    threading.Thread(
        target=_external_regenerate_job,
        kwargs={
            "repo": repo,
            "proposal_id": proposal_id,
            "proposal_dir": proposal_dir,
            "agent": inputs.agent,
            "prior_draft": inputs.prior_draft,
            "prompt_template_ids": inputs.prompt_template_ids,
            "previous_revision_id": inputs.previous_revision_id,
            "topic_request": inputs.topic_request,
        },
        daemon=True,
    ).start()
    return {
        "dir": proposal_dir,
        "topics": proposal_dir / "topics.json",
        "wiki": wiki_path,
    }


def _external_regenerate_job(
    *,
    repo: Path,
    proposal_id: str,
    proposal_dir: Path,
    agent: str | None,
    prior_draft: dict[str, Any] | None,
    prompt_template_ids: list[str] | None = None,
    previous_revision_id: int | None = None,
    topic_request: str | None = None,
) -> None:
    try:
        templates = _resolve_prompt_templates(prompt_template_ids)
        proposals, wiki = _draft_proposal(
            repo=repo,
            out_dir=proposal_dir,
            proposal_id=proposal_id,
            topic_request=topic_request,
            agent=agent,
            prior_draft=prior_draft,
            prompt_templates=templates,
        )
        proposals["status"] = "pending_review"
        _reset_review_markers_for_regenerate(proposals)
        _write_proposal_artifacts(
            proposal_dir, proposals=proposals, wiki=wiki,
            repo_path=repo, proposal_id=proposal_id,
            append_revision=True,
            revision_kind="regenerated",
        )
        if prior_draft is not None:
            _mark_addressed_feedback_after_regenerate(
                repo, proposal_id, previous_revision_id,
                prior_draft["proposal"], wiki,
            )
        _mark_run_completed(proposal_dir)
        _maybe_review_note(repo, proposal_id)
    except Exception as exc:
        _record_thread_failure(proposal_dir, agent, exc)
    finally:
        run_control.release(proposal_id)


def regenerate_proposal_run(repo_path: str | Path, proposal_id: str) -> dict[str, Path]:
    """Re-draft a proposal via the external agent.

    Delegates to the background runner so the web worker never blocks on
    the agent subprocess; the in-flight guard lives in
    `start_external_regenerate_run`. Failures land in the run's status
    file rather than propagating.
    """
    return start_external_regenerate_run(repo_path, proposal_id)
