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


def _regenerate_agent_or_fallback(agent: str | None) -> str | None:
    """Reuse the prior run's agent only when it still names a configured agent.

    A regenerate reuses the original run's persisted `agent`, but that agent may
    have been renamed or removed from `topic_proposal_external_agents` since —
    and an explicit stale id short-circuits `_resolve_agent_config`'s fallback
    chain and blows up with `unknown external topic proposal agent`. Dropping it
    to `None` hands resolution back to that chain (drafting-surface binding →
    global default), so the regenerate uses the current related agent's command
    instead of the dead one. Mirrors the "binding is only ever an override"
    rule in `lib/prompts/agents.py`."""
    from lib.prompts import is_configured_agent

    return agent if is_configured_agent(agent) else None


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
        inputs = _resolve_regenerate_inputs_from_proposal(
            repo, proposal_id, prior_proposal, wiki_path,
        )
    else:
        inputs = _resolve_regenerate_inputs_from_status(repo, proposal_id)
    inputs.agent = _regenerate_agent_or_fallback(inputs.agent)
    return inputs


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
        # Reset the finish signal — a reused run id must not inherit a prior
        # run's `agent_signaled` (the ORM metadata merge only adds keys), or
        # this fresh run would be treated as already-ingested.
        "agent_signaled": False,
        "signaled_by": None,
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


def _already_ingested_by_agent(out_dir: Path) -> bool:
    """True when the agent already persisted via `proposal-finish`
    (notify-on-finish). The runner returns that persisted result, so the job
    must not re-write the artifacts and double-persist the proposal."""
    from lib.topics.proposal_external import load_status
    status = load_status(out_dir) or {}
    return bool(status.get("agent_signaled"))


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
        if not _already_ingested_by_agent(out_dir):
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


def _resolve_drift_scope(repo: Path, proposal_id: str) -> dict[str, Any]:
    """Topics on this run that still carry an open content-drift note — the
    scope a content-drift regenerate should re-derive, and nothing else.

    Returns ``{"topic_ids": [...], "drifted_paths": {id: [paths]}}``. An empty
    ``topic_ids`` means "no drift pending" → the regenerate stays a full
    re-draft (unchanged behaviour, e.g. a manual Regenerate on a clean run)."""
    from lib.topics.content_drift import CONTENT_DRIFT_THREAD_KIND
    from lib.topics.proposal_orm import orm_open_content_drift_threads

    topic_ids: list[str] = []
    drifted_paths: dict[str, list[str]] = {}
    for thread in orm_open_content_drift_threads(
        repo, kind=CONTENT_DRIFT_THREAD_KIND, proposal_id=proposal_id
    ):
        topic_id = thread.get("topic_id")
        if not topic_id or topic_id in drifted_paths:
            continue
        topic_ids.append(topic_id)
        drifted_paths[topic_id] = list(thread.get("drifted_paths") or [])
    return {"topic_ids": topic_ids, "drifted_paths": drifted_paths}


def _prior_topic_ids(inputs: "_RegenerateInputs") -> set[str]:
    """Ids of the topics already in the run's prior draft — the set a
    caller-chosen regenerate scope is validated against."""
    prior = (inputs.prior_draft or {}).get("proposal") or {}
    return {t.get("id") for t in prior.get("topics") or [] if t.get("id")}


def _scope_for_regenerate(
    repo: Path, proposal_id: str, inputs: "_RegenerateInputs",
    topic_ids: list[str] | None,
) -> dict[str, Any]:
    """The scope a regenerate should re-derive.

    A caller-chosen `topic_ids` subset (validated against the run's own topics)
    takes precedence — that is the user picking which wikis to refresh. When
    none is given (or none is valid), fall back to the drift-derived scope,
    which is empty for a clean run ⇒ full re-draft. Drift paths are carried
    onto chosen topics where known so the prompt can point at the changed files."""
    drift = _resolve_drift_scope(repo, proposal_id)
    if not topic_ids:
        return drift
    valid = _prior_topic_ids(inputs)
    chosen = [t for t in dict.fromkeys(topic_ids) if t in valid]
    if not chosen:
        return drift
    drifted_paths = drift.get("drifted_paths") or {}
    return {"topic_ids": chosen,
            "drifted_paths": {t: drifted_paths.get(t, []) for t in chosen}}


def start_external_regenerate_run(
    repo_path: str | Path,
    proposal_id: str,
    *,
    topic_ids: list[str] | None = None,
) -> dict[str, Path]:
    """Start an external-agent regenerate job in the background.

    `topic_ids` optionally narrows the redraft to a caller-chosen subset of the
    run's topics; when omitted the run's open content-drift notes decide the
    scope (empty ⇒ full re-draft)."""
    repo = Path(repo_path)
    proposal_dir = topic_dir(repo) / "proposals" / proposal_id
    wiki_path = proposal_dir / "wiki.md"
    # Block concurrent regenerates racing on the same proposal_id.
    _guard_regenerate_not_in_flight(repo, proposal_id)

    inputs = _resolve_regenerate_inputs(repo, proposal_id)
    # Scope the redraft (empty ⇒ full re-draft). Persisted on the run status so
    # the splice can recover it in the agent's own process on the
    # notify-on-finish path.
    scope = _scope_for_regenerate(repo, proposal_id, inputs, topic_ids)

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
        # Regenerate reuses the run id, so the prior completed run left
        # `agent_signaled=True` in metadata. Clear it (the merge can't delete
        # keys) or this regenerate would short-circuit as already-ingested.
        "agent_signaled": False,
        "signaled_by": None,
        # Splice input for both ingest paths (runner exit + proposal-finish).
        # Distinct key from ProposalRun's legacy `regenerate_scope` String
        # column ("run"/"topic"); this rides in the status metadata bag.
        "regenerate_drift_scope": scope,
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
            "scope": scope,
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
    scope: dict[str, Any] | None = None,
) -> None:
    try:
        templates = _resolve_prompt_templates(prompt_template_ids)
        # A scoped regenerate tells the agent to re-derive only the drifted
        # topics; the splice (in the ingest path) preserves the rest verbatim.
        if prior_draft is not None and scope and scope.get("topic_ids"):
            prior_draft = {
                **prior_draft,
                "scope_topic_ids": scope["topic_ids"],
                "scope_drifted_paths": scope.get("drifted_paths") or {},
            }
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
        # The agent may have already ingested via `proposal-finish`
        # (notify-on-finish), which appends the `regenerated` revision
        # itself — don't append a second, duplicate one here.
        if not _already_ingested_by_agent(proposal_dir):
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


def regenerate_proposal_run(
    repo_path: str | Path, proposal_id: str,
    *, topic_ids: list[str] | None = None,
) -> dict[str, Path]:
    """Re-draft a proposal via the external agent.

    `topic_ids` optionally narrows the redraft to a caller-chosen subset of the
    run's topics (the rest are preserved verbatim). When omitted, the run's
    open content-drift notes decide the scope (or a full re-draft when there
    are none).

    Delegates to the background runner so the web worker never blocks on
    the agent subprocess; the in-flight guard lives in
    `start_external_regenerate_run`. Failures land in the run's status
    file rather than propagating.
    """
    return start_external_regenerate_run(repo_path, proposal_id, topic_ids=topic_ids)
