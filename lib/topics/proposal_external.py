"""External agent runner for topic proposal generation."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lib.trace import trace_service
from lib.settings import settings
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.proposal_drafting import format_review_feedback_for_prompt
from lib.topics import TopicGraphError, utc_now


STATUS_FILE = "status.json"
OUTPUT_FILE = "agent-output.json"
TEMP_OUTPUT_DIR = ".tmp"
TEMP_OUTPUT_FILE = "agent-output.json"
FAILURE_SUMMARY_LIMIT = 500
FAILURE_DETAIL_LIMIT = 4000
FAILURE_STREAM_TAIL_LIMIT = 2000
PIPELINE_SESSION_TITLE = "Topic proposal pipeline (evidence → draft → review)"


@dataclass(frozen=True)
class FailureDiagnostics:
    summary: str
    detail: str
    stdout_tail: str
    stderr_tail: str


def external_agent_configured() -> bool:
    return bool(settings.topic_proposal_external_agents)


def default_external_agent_id() -> str | None:
    if "claude" in settings.topic_proposal_external_agents:
        return "claude"
    if "codex" in settings.topic_proposal_external_agents:
        return "codex"
    return next(iter(settings.topic_proposal_external_agents), None)


def external_trace_id(proposal_id: str) -> str:
    return f"topic-proposal-{proposal_id}"


def _finish_command(repo: Path, proposal_id: str) -> str:
    """The exact shell command the drafting agent runs as its final step to
    signal completion. Built with the server's interpreter + regin CLI path
    and an explicit `--repo`, so it works regardless of the target repo or
    whether `regin` is on the agent's PATH."""
    cli = settings.project_root / "cli" / "regin.py"
    parts = [
        sys.executable, str(cli), "topics", "proposal-finish",
        proposal_id, "--repo", str(repo),
    ]
    return " ".join(shlex.quote(part) for part in parts)


def _repo_and_id_from_out_dir(out_dir: Path) -> tuple[Path, str]:
    """Derive (repo_path, proposal_id) from an out_dir.

    out_dir is always `<repo>/.regin/topics/proposals/<id>` — this
    convention has been load-bearing since the proposal feature
    shipped and is locked in by `lib.topics.core.topic_dir`.
    """
    proposal_id = out_dir.name
    repo_path = out_dir.parents[3]  # proposals/ → topics/ → .regin/ → repo
    return repo_path, proposal_id


def notify_proposal_ready(
    repo: Path, proposal_id: str, agent: str | None,
    session_trace_id: str | None = None,
) -> None:
    """Surface a finished external-agent proposal as an inbox event whose
    action link opens the *specific* proposal run in the Topics review
    workspace.

    `session_trace_id` is the real drafting-agent Claude Code session (the run's
    `agent_trace_id`), used as the message's `trace_id` so the card's footer
    "session" link resolves to the actual drafting session rather than the
    synthetic `topic-proposal-<id>` orchestration wrapper. It falls back to that
    wrapper id when the drafting session isn't known.

    Emitted from exactly ONE completion path per run. When the agent signals
    via `proposal-finish` (`lib.topics.proposals.finish`, the authoritative
    completion in the notify-on-finish design), *that* path notifies and the
    server-runner exit deliberately does NOT re-notify on its signalled branch.
    Only when the agent exits *without* signalling does the runner exit notify
    instead (the fallback). Keeping it to one emit matters because supersede
    dedups the inbox *card* but NOT the push channels: `record_message` pushes
    on every write (supersede included), so a second emit double-notifies
    Feishu/webhook even though only one card shows. `events.emit` is itself
    best-effort and gated by `proposal.ready`'s enablement."""
    from lib.agent_messages import events
    events.emit(
        "proposal.ready",
        trace_id=session_trace_id or external_trace_id(proposal_id),
        title=f"Proposal ready: {proposal_id}",
        body=(f"External agent **{agent or 'external agent'}** finished "
              f"drafting proposal **{proposal_id}**. Review, apply, or "
              f"regenerate it in the Topics view."),
        key=f"proposal-ready:{proposal_id}",
        links=[{"label": "Open proposal run",
                "href": events.proposal_url(repo, proposal_id)}])


def _notify_proposal_ready(ctx: "_AgentRunContext") -> None:
    """Runner-exit wrapper: derive the repo from the out_dir and read the
    drafting-agent session id the runner/finish path recorded, then delegate to
    `notify_proposal_ready`. The try only shields the repo-path derivation."""
    try:
        repo_path, _ = _repo_and_id_from_out_dir(ctx.out_dir)
    except (IndexError, OSError, AttributeError):
        return
    status = load_status(ctx.out_dir) or {}
    notify_proposal_ready(
        repo_path, ctx.proposal_id, ctx.agent,
        session_trace_id=status.get("agent_trace_id"))


def write_status(out_dir: Path, status: dict[str, Any]) -> dict[str, Any]:
    """Persist proposal run status to the ORM (the authoritative store).

    Phase E2 made the ORM the source of truth; the disk `status.json` is no
    longer written, so `load_status` reads it disk-first only for legacy
    dirs and otherwise falls back to the ORM. The finish signal therefore
    crosses the agent-subprocess → server boundary through the shared
    SQLite DB (both resolve the same file), not through disk.
    """
    from lib.topics.proposal_orm import (
        orm_create_proposal_run, orm_update_proposal_status,
    )
    status = {**status, "updated_at": utc_now()}
    repo_path, proposal_id = _repo_and_id_from_out_dir(out_dir)

    column_fields = {
        "state", "trace_id", "started_at", "completed_at",
        "error", "error_detail",
    }
    # `proposal_status` is the *review-state* axis (pending_review →
    # ready_to_apply → applied), owned solely by the proposal-save / apply
    # paths. It is orthogonal to the run *lifecycle* status this function
    # writes. `load_status` (→ _run_to_status_dict) spreads the whole
    # metadata bag — including proposal_status — into the status dict, so
    # without this exclusion any write_status round-trips a stale value
    # back over a fresh one: a regenerate's finish ingest sets the new
    # revision to pending_review, then write_status re-stamps the prior
    # `applied`, stranding the draft as un-appliable. Never carry it here.
    review_state_fields = {"proposal_status"}
    metadata_patch = {
        k: v for k, v in status.items()
        if k not in column_fields
        and k not in review_state_fields
        and k not in {"agent", "updated_at"}
    }

    orm_create_proposal_run(
        repo_path, proposal_id,
        provider="external-agent",
        state=status.get("state") or "queued",
        agent=status.get("agent"),
        started_at=status.get("started_at"),
        prompt_template_ids=status.get("prompt_template_ids") or [],
        metadata=metadata_patch,
    )
    orm_update_proposal_status(
        repo_path, proposal_id,
        state=status.get("state"),
        completed_at=status.get("completed_at"),
        error=status.get("error"),
        error_detail=status.get("error_detail"),
        metadata_patch=metadata_patch,
        clear_error="error" in status,
        clear_error_detail="error_detail" in status,
        clear_completed_at="completed_at" in status,
    )
    return status


def load_status(out_dir: Path) -> dict[str, Any] | None:
    """Disk-first read of proposal status during the dual-write window;
    ORM fallback when the disk file is missing."""
    path = out_dir / STATUS_FILE
    if path.exists():
        return json.loads(path.read_text())
    from lib.topics.proposal_orm import orm_load_proposal_status
    repo_path, proposal_id = _repo_and_id_from_out_dir(out_dir)
    return orm_load_proposal_status(repo_path, proposal_id)


def _wiki_section_headings(repo: Path, topic_id: str, *, limit: int = 14) -> list[str]:
    """The `## ` headings of a topic's wiki page — the concrete territory it
    already documents — so a drafting agent can steer a new topic *around* it
    instead of restating it. Best effort: a missing/unreadable page yields []."""
    from lib.topics.wiki import wiki_dir
    try:
        text = (wiki_dir(repo) / f"{topic_id}.md").read_text(encoding="utf-8")
    except Exception:
        return []
    heads: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            heads.append(line[3:].strip())
            if len(heads) >= limit:
                break
    return heads


def _existing_topics_summary(repo: Path) -> list[dict[str, Any]]:
    """Existing approved topics, enriched enough that the agent can draft
    *around* them rather than only avoiding an id/alias clash: id/label/aliases,
    the topic's bucket (`parent_id`), a one-line `covers` (its blurb, or a
    trimmed intent), and the `## ` section headings of its wiki (`wiki_sections`
    — the territory already covered). The agent still explores the repo itself;
    this is the boundary map, not an evidence pack.
    """
    try:
        graph = load_authoritative_graph(repo)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for tid, topic in sorted((graph.get("topics") or {}).items()):
        if not isinstance(topic, dict):
            continue
        covers = (topic.get("blurb") or topic.get("intent") or "").strip()
        if len(covers) > 240:
            covers = covers[:237] + "..."
        entry: dict[str, Any] = {
            "id": tid,
            "label": topic.get("label"),
            "aliases": topic.get("aliases", []),
            "parent_id": topic.get("parent_id"),
            "covers": covers,
        }
        sections = _wiki_section_headings(repo, tid)
        if sections:
            entry["wiki_sections"] = sections
        out.append(entry)
    return out


def _bucket_summary(repo: Path) -> list[dict[str, Any]]:
    """The top-level taxonomy buckets (id + label + blurb) a proposed topic
    can be placed under via `parent_id`. The reserved `unclassified` bucket
    is omitted — leaving `parent_id` null routes there, it's not a target."""
    try:
        graph = load_authoritative_graph(repo)
    except Exception:
        return []
    return [
        {"id": tid, "label": topic.get("label"),
         "blurb": topic.get("blurb") or topic.get("intent", "")}
        for tid, topic in sorted((graph.get("topics") or {}).items())
        if topic.get("kind") == "bucket" and tid != "unclassified"
    ]


def _format_template_section(prompt_templates: list[dict[str, Any]] | None) -> str:
    """Render injected prompt templates as a ## Custom Instructions block.

    Sits between the user's topic_request and the Rules block. Empty
    list (and None) collapse to no extra text, so the prompt is byte-
    identical for runs without templates.
    """
    if not prompt_templates:
        return ""
    lines = ["", "", "## Custom Instructions"]
    for template in prompt_templates:
        label = template.get("label") or template.get("slug") or "Template"
        body = (template.get("body") or "").strip()
        if not body:
            continue
        lines.extend(["", f"### {label}", "", body])
    return "\n".join(lines)


def _template_slugs(prompt_templates: list[dict[str, Any]] | None) -> list[str]:
    """Slug list for the injected prompt templates (skips entries without one)."""
    return [str(t.get("slug")) for t in (prompt_templates or []) if t.get("slug")]


def _resolve_agent_config(agent_id: str | None) -> tuple[str, Any]:
    """Resolve the agent id + its configured launch spec, or raise ValueError.

    Precedence: an explicit ``agent_id`` (the run request's picker) → the
    drafting surface's bound agent → the global default. So binding the
    ``topic-proposal-drafting`` goal to an agent routes every unspecified run to
    it, while an explicit per-run pick still wins."""
    from lib.prompts import surface_agent
    from lib.prompts.surfaces.drafting import SURFACE_ID as DRAFTING_SURFACE_ID

    agent = agent_id or surface_agent(DRAFTING_SURFACE_ID) or default_external_agent_id()
    if not agent:
        raise ValueError(
            "external-agent proposal provider requires topic_proposal_external_agents "
            "in settings.local.json"
        )
    config = settings.topic_proposal_external_agents.get(agent)
    if config is None:
        raise ValueError(f"unknown external topic proposal agent: {agent}")
    return agent, config


@dataclass(frozen=True)
class _AgentRunContext:
    """Immutable per-run paths/ids shared between spawn and output handling.

    Bundled so `_handle_agent_output` takes one context arg instead of a
    dozen positional ones.
    """

    repo: Path
    out_dir: Path
    trace_id: str
    proposal_id: str
    agent: str
    before_topic: Any
    temp_output_path: Path
    output_path: Path
    stdout_path: Path
    stderr_path: Path
    started: float
    prompt_templates: list[dict[str, Any]] | None


def run_external_agent_proposal(
    *,
    repo: Path,
    out_dir: Path,
    proposal_id: str,
    topic_request: str | None = None,
    agent_id: str | None = None,
    prior_draft: dict[str, Any] | None = None,
    prompt_templates: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str]:
    agent, config = _resolve_agent_config(agent_id)

    trace_id = external_trace_id(proposal_id)
    status = {
        "state": "running",
        "trace_id": trace_id,
        "agent": agent,
        "started_at": utc_now(),
        "completed_at": None,
        "error": None,
        "pid": None,
        "prompt_template_ids": _template_slugs(prompt_templates),
        # The ORM metadata merge is additive, so a prior attempt's
        # validation-failure marker survives a fresh start unless it is
        # explicitly overwritten — and a stale marker re-opens the finish
        # retry gate for failures that are NOT fixable agent output.
        "failed_validation": False,
        "validation_errors": None,
    }
    write_status(out_dir, status)
    _emit_session_start(trace_id, proposal_id=proposal_id, repo=repo, agent=agent)
    _emit(trace_id, "proposal.agent.start", {"proposal_id": proposal_id, "agent": agent, "repo": str(repo)})

    before_topic = _read_topic_signature(repo)
    temp_dir = out_dir / TEMP_OUTPUT_DIR
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_output_path = temp_dir / TEMP_OUTPUT_FILE
    output_path = out_dir / OUTPUT_FILE
    if temp_output_path.exists():
        temp_output_path.unlink()
    instructions = _instructions(
        repo,
        topic_request,
        out_dir,
        temp_output_path,
        prior_draft=prior_draft,
        prompt_templates=prompt_templates,
    )
    (out_dir / "instructions.md").write_text(instructions)
    _emit(trace_id, "proposal.agent.instructions", {"proposal_id": proposal_id, "path": str(out_dir / "instructions.md")})

    stdout_path = out_dir / "stdout.log"
    stderr_path = out_dir / "stderr.log"
    env = {
        **os.environ,
        "REGIN_TOPIC_PROPOSAL_DIR": str(out_dir),
        "REGIN_TOPIC_PROPOSAL_OUTPUT": str(temp_output_path),
        "REGIN_TOPIC_PROPOSAL_CANONICAL_OUTPUT": str(output_path),
        "REGIN_TOPIC_PROPOSAL_TRACE_ID": trace_id,
        "REGIN_TOPIC_PROPOSAL_ID": proposal_id,
        "REGIN_TOPIC_PROPOSAL_FINISH_CMD": _finish_command(repo, proposal_id),
    }
    cwd = Path(config.cwd).expanduser() if config.cwd else repo
    command = [config.command, *config.args]
    started = time.monotonic()
    ctx = _AgentRunContext(
        repo=repo,
        out_dir=out_dir,
        trace_id=trace_id,
        proposal_id=proposal_id,
        agent=agent,
        before_topic=before_topic,
        temp_output_path=temp_output_path,
        output_path=output_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        started=started,
        prompt_templates=prompt_templates,
    )
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return _fail(out_dir, trace_id, status, "failed", f"external agent failed to start: {exc}", exc)

    # Register the live process so the Stop endpoint (a different request
    # thread) can terminate it; happens before we block on communicate().
    from lib.topics.proposals import run_control
    run_control.register(proposal_id, proc)
    status["pid"] = proc.pid
    write_status(out_dir, status)
    # The agent signals completion by calling `regin topics proposal-finish`
    # (notify-on-finish), so the server-side wait has no fixed timeout by
    # default — a long draft is never killed mid-flight. A configured
    # ceiling (proposal_run_timeout_seconds > 0) is a backstop, not the
    # completion authority; a signalled-then-lingering process is still a
    # success at the ceiling.
    wait_timeout = _proposal_wait_timeout()
    try:
        stdout, stderr = proc.communicate(instructions, timeout=wait_timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        stdout, stderr = proc.communicate()
        stdout_path.write_text(stdout or "")
        stderr_path.write_text(stderr or "")
        signaled = _load_signaled_result(ctx)
        if signaled is not None:
            # The agent already signalled via `proposal-finish`, which is the
            # authoritative completion and *already emitted* `proposal.ready`
            # (see finish.py). Re-notifying here supersedes the same inbox card
            # but re-fires the push channels (record_message pushes on every
            # write, supersede included), double-notifying Feishu/webhook.
            return signaled
        return _fail(
            out_dir,
            trace_id,
            status,
            "timed_out",
            f"external agent timed out after {wait_timeout}s",
            exc,
            stdout=stdout,
            stderr=stderr,
        )
    return _handle_agent_output(ctx, proc, stdout, stderr, status)


def _proposal_wait_timeout() -> int | None:
    """Server-side ceiling (seconds) for blocking on the agent subprocess.
    0 (the default) → no ceiling: completion comes from the finish signal
    and the trace reaper, not a timer."""
    ceiling = settings.topic_evolution.proposal_run_timeout_seconds
    return ceiling if ceiling and ceiling > 0 else None


def _load_signaled_result(ctx: _AgentRunContext) -> tuple[dict[str, Any], str] | None:
    """If the agent already called `proposal-finish` (notify-on-finish), the
    proposal + wiki are persisted; load and return them so the runner skips
    a redundant re-ingest. Returns None when no finish signal was recorded."""
    status = load_status(ctx.out_dir)
    if not status or not status.get("agent_signaled"):
        return None
    from lib.topics.proposals.core_io import load_proposal
    proposal = load_proposal(ctx.repo, ctx.proposal_id)
    wiki_path = ctx.out_dir / "wiki.md"
    wiki = wiki_path.read_text() if wiki_path.exists() else (proposal.get("wiki") or "")
    return proposal, wiki


def _handle_agent_output(
    ctx: _AgentRunContext,
    proc: subprocess.Popen,
    stdout: str,
    stderr: str,
    status: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Post-`communicate` handling: cancellation, failures, payload parse.

    Split out of `run_external_agent_proposal` so the cancel check has one
    clear home and the parent stays under the complexity budget. The
    cancel check runs FIRST so a terminated subprocess (non-zero exit) is
    reported as `cancelled`, never `failed`.
    """
    from lib.topics.proposals import run_control
    if run_control.is_cancelled(ctx.proposal_id):
        return _cancelled(ctx, status, stdout=stdout, stderr=stderr)

    duration_ms = int((time.monotonic() - ctx.started) * 1000)
    ctx.stdout_path.write_text(stdout or "")
    ctx.stderr_path.write_text(stderr or "")
    # Notify-on-finish: if the agent already ingested via `proposal-finish`,
    # that path is authoritative — return its persisted result and skip the
    # legacy stdout/exit-code reject + re-ingest below (kept as the fallback
    # for agents that exit without signalling).
    signaled = _load_signaled_result(ctx)
    if signaled is not None:
        _emit(ctx.trace_id, "proposal.agent.complete", {"proposal_id": ctx.proposal_id, "agent": ctx.agent, "duration_ms": duration_ms, "signaled": True}, status_code="OK")
        _emit_session_end(ctx.trace_id, reason="completed")
        # No `_notify_proposal_ready` here: the agent's `proposal-finish`
        # self-ingest is authoritative and already emitted `proposal.ready`.
        # Re-emitting would re-push the notification (Feishu/webhook fire on
        # every record_message, supersede included) — a duplicate. The notify
        # lives only on the non-signalled fallback below, where finish never ran.
        return signaled
    if stdout:
        _emit(ctx.trace_id, "proposal.agent.stdout", {"proposal_id": ctx.proposal_id, "chunk": stdout[-4000:]})
    if stderr:
        _emit(ctx.trace_id, "proposal.agent.stderr", {"proposal_id": ctx.proposal_id, "chunk": stderr[-4000:]})

    _reject_bad_agent_result(ctx, proc, stdout, stderr, status)  # raises _fail on any problem

    agent_trace_id = _find_agent_session_trace_id(ctx.proposal_id, ctx.started)
    if agent_trace_id:
        status["agent_trace_id"] = agent_trace_id
        status["agent_trace_url"] = f"/trace/sessions/{agent_trace_id}"
        write_status(ctx.out_dir, status)

    try:
        proposal, wiki = _persist_agent_payload(ctx, stdout)
    except Exception as exc:
        return _fail(
            ctx.out_dir, ctx.trace_id, status, "failed",
            f"external agent output invalid: {exc}", exc,
            validation_errors=_validation_error_list(exc),
        )

    proposal["provider"] = "external-agent"
    proposal["metadata"] = {
        "agent": ctx.agent,
        "trace_id": ctx.trace_id,
        "agent_trace_id": agent_trace_id,
        "duration_ms": duration_ms,
        "output_contract": "regin-topic-proposal-external-v1",
        "temp_output": str(ctx.temp_output_path),
        "canonical_output": str(ctx.output_path),
        "prompt_template_ids": _template_slugs(ctx.prompt_templates),
    }
    status["state"] = "completed"
    status["completed_at"] = utc_now()
    # A regenerate reuses the run id, so a prior cycle's validation failure
    # would otherwise linger in the ORM metadata bag across this success.
    status["failed_validation"] = False
    status["validation_errors"] = None
    write_status(ctx.out_dir, status)
    _emit(ctx.trace_id, "proposal.agent.complete", {"proposal_id": ctx.proposal_id, "agent": ctx.agent, "duration_ms": duration_ms}, status_code="OK")
    _emit_session_end(ctx.trace_id, reason="completed")
    _notify_proposal_ready(ctx)
    return proposal, wiki


def _reject_bad_agent_result(
    ctx: _AgentRunContext,
    proc: subprocess.Popen,
    stdout: str,
    stderr: str,
    status: dict[str, Any],
) -> None:
    """Raise via `_fail` if the agent hit a permission prompt, exited
    non-zero, or mutated the approved graph. Returns normally otherwise."""
    if _looks_like_permission_prompt(f"{stdout}\n{stderr}"):
        _emit(ctx.trace_id, "proposal.agent.permission_request", {"proposal_id": ctx.proposal_id, "agent": ctx.agent})
        _fail(
            ctx.out_dir, ctx.trace_id, status, "waiting_for_permission",
            "external agent requested interactive permission; v1 runs non-interactively",
            stdout=stdout, stderr=stderr,
        )
    if proc.returncode != 0:
        _fail(
            ctx.out_dir, ctx.trace_id, status, "failed",
            f"external agent exited with code {proc.returncode}",
            stdout=stdout, stderr=stderr,
        )
    if _read_topic_signature(ctx.repo) != ctx.before_topic:
        _fail(ctx.out_dir, ctx.trace_id, status, "failed", "external agent modified the approved topic graph")


def _persist_agent_payload(
    ctx: _AgentRunContext, stdout: str,
) -> tuple[dict[str, Any], str]:
    """Parse + validate the agent's JSON and copy it to the canonical path."""
    payload = _load_agent_payload(ctx.temp_output_path, stdout)
    proposal, wiki = _normalise_agent_payload(ctx.repo, payload)
    _validate_paths(ctx.repo, proposal)
    proposal, wiki = _apply_regenerate_scope(ctx.repo, ctx.out_dir, proposal, wiki)
    if ctx.temp_output_path.exists():
        shutil.copyfile(ctx.temp_output_path, ctx.output_path)
    else:
        rendered_payload = json.dumps(payload, indent=2) + "\n"
        ctx.temp_output_path.write_text(rendered_payload)
        ctx.output_path.write_text(rendered_payload)
    return proposal, wiki


def _cancelled(
    ctx: _AgentRunContext,
    status: dict[str, Any],
    *,
    stdout: str | None = None,
    stderr: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Stamp the terminal `cancelled` status for a user-initiated stop, then raise.

    `cancelled` carries no `error` (a stop isn't a failure) and sits
    outside the {queued,running,completed} sets that
    `_apply_status_invariants` and the read-time serializer coerce, so it
    survives as a distinct terminal state. Raising means the job wrapper's
    success path (artifact write + `completed` stamp) never runs, and
    `_record_thread_failure` sees `cancelled` and leaves it alone.
    """
    status["state"] = "cancelled"
    status["completed_at"] = utc_now()
    status["error"] = None
    write_status(ctx.out_dir, status)
    _emit(
        ctx.trace_id, "proposal.agent.cancelled",
        {"proposal_id": ctx.proposal_id},
        status_code="ERROR", status_message="stopped by user",
    )
    _emit_session_end(ctx.trace_id, reason="cancelled")
    raise TopicGraphError("Proposal run stopped by user")


def _validation_error_list(exc: Exception) -> list[str]:
    """The machine-readable error list for an invalid-output failure:
    the per-field errors when the exception carries them
    (`ProposalValidationError`), else the exception text itself."""
    return list(getattr(exc, "errors", None) or [str(exc)])


def _fail(
    out_dir: Path,
    trace_id: str,
    status: dict[str, Any],
    state: str,
    message: str,
    exc: Exception | None = None,
    *,
    stdout: str | None = None,
    stderr: str | None = None,
    validation_errors: list[str] | None = None,
) -> tuple[dict[str, Any], str]:
    diagnostics = _failure_diagnostics(stdout=stdout, stderr=stderr)
    if diagnostics.summary:
        message = f"{message}: {diagnostics.summary}"
    if validation_errors is not None:
        # Marks the failure as a fixable agent-output problem: a later
        # `proposal-finish` re-signal is allowed to retry the ingest.
        status["validation_errors"] = validation_errors
        status["failed_validation"] = True
    else:
        # Attempt-scoped: a non-validation failure must close the retry
        # gate even when an earlier attempt's marker is in the merged bag.
        status["validation_errors"] = None
        status["failed_validation"] = False
    status["state"] = state
    status["error"] = message
    status["error_detail"] = diagnostics.detail or None
    status["stdout_tail"] = diagnostics.stdout_tail or None
    status["stderr_tail"] = diagnostics.stderr_tail or None
    status["completed_at"] = utc_now()
    write_status(out_dir, status)
    _emit(trace_id, "proposal.agent.failure", {"error": message}, status_code="ERROR", status_message=message)
    _emit_session_end(trace_id, reason=state)
    raise TopicGraphError(message) from exc


def _failure_diagnostics(*, stdout: str | None = None, stderr: str | None = None) -> FailureDiagnostics:
    stdout_tail = _tail_text(stdout, FAILURE_STREAM_TAIL_LIMIT)
    stderr_tail = _tail_text(stderr, FAILURE_STREAM_TAIL_LIMIT)
    combined = "\n\n".join(part for part in (stdout_tail, stderr_tail) if part)
    return FailureDiagnostics(
        summary=_failure_summary(combined),
        detail=_tail_text(combined, FAILURE_DETAIL_LIMIT),
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
    )


def _failure_summary(output: str | None) -> str:
    if not output:
        return ""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ""
    detail = " ".join(lines[-3:])
    return _tail_text(detail, FAILURE_SUMMARY_LIMIT)


def _tail_text(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return "..." + text[-(limit - 3):]


def _emit(
    trace_id: str,
    name: str,
    attributes: dict[str, Any],
    *,
    status_code: str = "UNSET",
    status_message: str | None = None,
) -> None:
    now = utc_now()
    span_id = f"{name}-{time.time_ns()}"
    attrs = {"agent_type": "topic-proposal-agent", **attributes}
    trace_service.ingest_session_spans([({
        "trace_id": trace_id,
        "span_id": span_id,
        "name": name,
        "start_time": now,
        "end_time": now,
        "duration_ms": 0,
        "status_code": status_code,
        "status_message": status_message,
    }, attrs)])


def _emit_session_start(trace_id: str, *, proposal_id: str, repo: Path, agent: str) -> None:
    _emit(
        trace_id,
        "session.start",
        {
            "source": "startup",
            "proposal_id": proposal_id,
            "repo": str(repo),
            "agent": agent,
        },
    )
    _emit(
        trace_id,
        "session.title",
        {
            "text": PIPELINE_SESSION_TITLE,
            "source": "claude_ai_title",
            "proposal_id": proposal_id,
        },
    )


def _emit_session_end(trace_id: str, *, reason: str) -> None:
    _emit(
        trace_id,
        "session.end",
        {
            "reason": reason,
        },
    )


def _prior_proposal_for_prompt(proposal: dict[str, Any] | None) -> dict[str, Any]:
    """Drop agent self-`notes` from the prior draft before it goes into a
    regenerate prompt.

    `notes` is the previous pass's editorial about its own choices (e.g.
    "these three topics replace the prior trace-span-repair draft"). It is
    not draft content, and replaying it biases the next draft toward
    repeating that reasoning. After a restore it is doubly wrong: the notes
    describe a draft the user just discarded, while run-level `notes` are
    never reverted to the restored revision's.
    """
    clean = {k: v for k, v in (proposal or {}).items() if k != "notes"}
    metadata = clean.get("metadata")
    if isinstance(metadata, dict) and "notes" in metadata:
        clean["metadata"] = {k: v for k, v in metadata.items() if k != "notes"}
    return clean


def _sibling_refresh_section(repo: Path, out_dir: Path) -> str:
    """The "Sibling topics being refreshed" prompt section, or "" when this
    isn't a content-drift refresh batch with ≥1 other pending sibling.

    `out_dir` is `<repo>/.regin/topics/proposals/<id>`, so its name is the
    proposal id (see `_repo_and_id_from_out_dir`). For user/external proposals
    the id isn't in the content-drift set, so the helper returns "" and no
    section is emitted."""
    from lib.topics.agent_spawn import _sibling_refresh_context
    block = _sibling_refresh_context(repo, out_dir.name)
    if not block:
        return ""
    return (
        "\nSibling topics being refreshed in this same batch (keep your "
        "cross-references — edges and wiki mentions — consistent with these; "
        "do NOT treat their approved summary as final, they are being "
        f"rewritten too):\n\n{block}\n"
    )


def _scoped_refresh_directive(prior_draft: dict[str, Any]) -> str:
    """Regenerate-only block that narrows the redraft to the drifted topics.

    Empty unless the run is a scoped content-drift regenerate. When present it
    names exactly the topics whose refs drifted (with the drifted files) and
    tells the agent to re-derive only those pages; every other topic is
    preserved verbatim by the splice, so the agent may omit them from its
    output entirely. Correctness does not depend on the agent obeying this —
    the splice discards any non-scoped topic bodies regardless — it only saves
    the agent from re-exploring untouched areas."""
    topic_ids = prior_draft.get("scope_topic_ids") or []
    if not topic_ids:
        return ""
    drifted_paths = prior_draft.get("scope_drifted_paths") or {}
    lines = []
    for topic_id in topic_ids:
        paths = drifted_paths.get(topic_id) or []
        listed = ", ".join(f"`{p}`" for p in paths) if paths else "(this topic's refs)"
        lines.append(f"- **{topic_id}** — drifted files: {listed}")
    listing = "\n".join(lines)
    return f"""Scoped refresh — the code changed under only these topics:
{listing}

Re-derive the wiki page for ONLY the topics listed above, from their current
refs. You may omit every other topic from your output: unchanged topics are
preserved as-is and must NOT be rewritten. Focus your exploration on the
drifted files.

"""


def _prior_reference_block(prior_draft: dict[str, Any] | None) -> str:
    """The regenerate-only 'Prior draft reference' block, or '' on a fresh run.

    Kept as a discrete builder so it can be passed as the ``prior_reference``
    variable into the editable drafting skeleton (see lib/prompts/surfaces)."""
    if not prior_draft:
        return ""
    feedback_reference = ""
    feedback_block = format_review_feedback_for_prompt(prior_draft.get("feedback_threads") or [])
    if feedback_block:
        feedback_reference = f"""
{feedback_block}

"""
    scope_reference = _scoped_refresh_directive(prior_draft)
    return f"""

Prior draft reference:
{feedback_reference}{scope_reference}Use the previous proposal and wiki only as reference — to keep good coverage and to address any review feedback above — not as a baseline to diff against. Re-check every topic against the current repository.

Write each topic's wiki and the notes as a standalone description of the repository as it is NOW. Do NOT write changelog or diff prose comparing this revision to the previous one: avoid phrasing like "was removed", "is now", "no longer", "the old …", "previously", "changed from", or "renamed to". The reader has never seen the prior draft — describe the current structure and behavior directly, citing files that exist today.

A regenerate REVISES the page in place; it does not accrete. Keep each wiki's scope and length close to the prior draft — correct what the current code no longer matches and cut detail that has gone stale, rather than appending new file-by-file descriptions because some files changed. A drift note asking you to refresh a topic is a request to re-verify and tighten its existing narrative, not to grow it.

Previous proposal JSON:
```json
{json.dumps(_prior_proposal_for_prompt(prior_draft.get('proposal')), indent=2, sort_keys=True)}
```

Previous wiki markdown:
```markdown
{str(prior_draft.get('wiki') or '')}
```
"""


def _instructions(
    repo: Path,
    topic_request: str | None,
    out_dir: Path,
    temp_output_path: Path,
    *,
    prior_draft: dict[str, Any] | None = None,
    prompt_templates: list[dict[str, Any]] | None = None,
) -> str:
    """Build the drafting agent's task prompt.

    The body is the editable ``topic-proposal-drafting`` surface
    (``lib/prompts/surfaces/drafting.py``); this function only assembles the
    runtime context it interpolates. A broken user edit degrades to the built-in
    default inside ``render_surface`` — the prompt is never left unbuildable.
    """
    from lib.prompts import render_surface
    from lib.prompts.surfaces.drafting import SURFACE_ID

    default_request = (
        "No specific topic request was provided. "
        "Propose the most useful uncovered topics from the repository."
    )
    context = {
        "topic_request": topic_request or default_request,
        "prior_reference": _prior_reference_block(prior_draft),
        "custom_instructions": _format_template_section(prompt_templates),
        "temp_output_path": str(temp_output_path),
        "output_file": str(out_dir / OUTPUT_FILE),
        "finish_cmd": _finish_command(repo, out_dir.name),
        "existing_topics_json": json.dumps(_existing_topics_summary(repo), indent=2, sort_keys=True),
        "buckets_json": json.dumps(_bucket_summary(repo), indent=2, sort_keys=True),
        "sibling_section": _sibling_refresh_section(repo, out_dir),
    }
    return render_surface(SURFACE_ID, context)


def _load_agent_payload(output_path: Path, stdout: str) -> dict[str, Any]:
    if output_path.exists():
        return json.loads(output_path.read_text())
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stdout, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    return json.loads(stdout)


def _find_agent_session_trace_id(proposal_id: str, started_monotonic: float) -> str | None:
    """Best-effort link from the wrapper run to the real tool-using agent trace."""
    try:
        from lib.orm.engine import get_connection

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        started_at = now - timedelta(seconds=max(0, time.monotonic() - started_monotonic) + 30)
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT trace_id, started_at, title, agent_type
                FROM sessions
                WHERE started_at >= ?
                  AND COALESCE(agent_type, '') != 'topic-proposal-agent'
                ORDER BY started_at DESC
                LIMIT 20
                """,
                (started_at.isoformat(),),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return None

    for row in rows:
        title = row["title"] or ""
        if "Regin Topic Proposal Agent Task" in title and proposal_id in title:
            return row["trace_id"]
    for row in rows:
        title = row["title"] or ""
        if "Regin Topic Proposal Agent Task" in title:
            return row["trace_id"]
    return None


def _topic_wiki_section(topic: dict[str, Any]) -> str | None:
    """One topic's contribution to the combined wiki, or None if it has no
    page. Normalizes to a level-2 (`## `) section so the combined doc always
    has section boundaries (a leading `# ` h1 is demoted to `## `, an h2 is
    kept, anything else is headed with the topic label)."""
    body = str(topic.get("wiki") or "").strip()
    if not body:
        return None
    if body.startswith("## "):
        return body
    if body.startswith("# "):
        return "#" + body  # demote h1 -> h2 so the combined doc keeps `##` boundaries
    label = topic.get("label") or topic.get("id") or "Topic"
    return f"## {label}\n\n{body}"


def _combined_proposal_wiki(payload: dict[str, Any], topics: list[dict[str, Any]]) -> str:
    """Derive the revision-level combined wiki from the per-topic pages.

    Per-topic `wiki` is the source of truth; this stitches an ``overview``
    intro plus one ``## label`` section per topic into a single document for
    revision-level storage, legacy display, and the shared intro. Falls back
    to a legacy top-level ``wiki`` string when no topic carries its own page.
    """
    sections = [s for s in (_topic_wiki_section(t) for t in topics) if s]
    if not sections:
        return str(payload.get("wiki") or "").strip()
    overview = str(payload.get("overview") or "").strip()
    parts = ([overview] if overview else []) + sections
    return "\n\n".join(parts).strip()


def _strip_review_markers(topics: list[Any]) -> None:
    """Review markers are server-owned bookkeeping; an agent echoing them
    from the prior-draft reference could smuggle a stale 'accepted' onto
    redrafted content that then survives the splice/reset."""
    from lib.topics.proposals._common import _REGENERATE_RESET_TOPIC_FIELDS
    for topic in topics:
        if isinstance(topic, dict):
            for field in _REGENERATE_RESET_TOPIC_FIELDS:
                topic.pop(field, None)


def _normalise_agent_payload(repo: Path, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    from lib.topics.proposal_drafting import (
        PROPOSAL_VERSION, ProposalValidationError, validate_proposal,
    )

    if payload.get("version") == PROPOSAL_VERSION and isinstance(payload.get("topics"), list):
        proposal = payload
    else:
        proposal = {
            "version": PROPOSAL_VERSION,
            "repo": repo.resolve().name,
            "scope": "all",
            "generated_at": utc_now(),
            "status": "draft",
            "topics": payload.get("topics") or [],
            "notes": payload.get("notes") or [],
        }
        # Carry the legacy top-level wiki through the wrap so the
        # require_wiki check sees the same fallback _combined_proposal_wiki
        # reads (pass-through payloads keep the key naturally).
        if "wiki" in payload:
            proposal["wiki"] = payload["wiki"]
    _strip_review_markers(proposal.get("topics") or [])
    wiki = _combined_proposal_wiki(payload, proposal.get("topics") or [])
    errors = validate_proposal(proposal, require_wiki=True)
    if errors:
        raise ProposalValidationError(errors)
    return proposal, wiki


def _regenerate_scope_topic_ids(out_dir: Path) -> list[str]:
    """The drifted topic ids a scoped content-drift regenerate must re-derive,
    read from the run status (set by `start_external_regenerate_run`). Empty on
    a fresh run or a full manual regenerate."""
    status = load_status(out_dir) or {}
    scope = status.get("regenerate_drift_scope") or {}
    ids = scope.get("topic_ids") or []
    return [tid for tid in ids if isinstance(tid, str) and tid]


def _pick_topic(
    topic: dict[str, Any], scope: set[str], drafted_by_id: dict[str, Any],
) -> dict[str, Any]:
    """The freshly drafted body for a scoped (drifted) topic, else the prior
    topic verbatim — so untouched pages stay byte-identical."""
    topic_id = topic.get("id")
    if topic_id in scope and topic_id in drafted_by_id:
        return drafted_by_id[topic_id]
    return topic


def _rebuild_combined_wiki(prior_wiki: str, merged_topics: list[dict[str, Any]]) -> str:
    """Prior intro + one `## ` section per (merged) per-topic wiki."""
    from lib.topics.wiki_sections import split_wiki_sections

    intro, _ = split_wiki_sections(prior_wiki)
    sections = [s for s in (_topic_wiki_section(t) for t in merged_topics) if s]
    parts = ([intro] if intro else []) + sections
    return "\n\n".join(parts).strip()


def _splice_scoped_topics(
    prior: dict[str, Any], drafted: dict[str, Any], prior_wiki: str,
    scope_topic_ids: list[str],
) -> tuple[dict[str, Any], str]:
    """Merge a scoped redraft back onto the prior full topic list.

    Only the scoped (drifted) topics take the freshly drafted body; every other
    topic is copied verbatim from the prior revision, so non-drifted wiki pages
    are byte-identical across the regenerate. The combined wiki is rebuilt from
    the prior intro plus one `## ` section per (merged) topic — keeping the full
    topic set means per-doc apply never loses a forward edge."""
    scope = set(scope_topic_ids)
    drafted_by_id = {
        t.get("id"): t for t in drafted.get("topics") or [] if t.get("id")
    }
    merged_topics = [_pick_topic(t, scope, drafted_by_id) for t in prior.get("topics") or []]
    merged = {**prior, "topics": merged_topics}
    # Drop the prior revision's stamped fields so the appended `regenerated`
    # revision is stamped fresh (`_append_new_revision` reuses a carried-over
    # `generated_at`); the inert `revision`/`revisions` keys would otherwise
    # ride along too.
    for stale_key in ("generated_at", "revision", "revisions"):
        merged.pop(stale_key, None)
    base_wiki = prior_wiki or str(prior.get("wiki") or "")
    merged_wiki = _rebuild_combined_wiki(base_wiki, merged_topics)
    return merged, (merged_wiki or base_wiki)


def _apply_regenerate_scope(
    repo: Path, out_dir: Path, proposal: dict[str, Any], wiki: str,
) -> tuple[dict[str, Any], str]:
    """If this run is a scoped content-drift regenerate, splice the drifted
    topics over the prior revision so untouched wikis stay byte-identical.

    A no-op (returns the drafted proposal unchanged) when the run isn't scoped,
    or when there's no prior revision to splice against (a fresh run). Called
    from BOTH ingest paths — the runner exit and `proposal-finish` — since the
    agent may self-ingest in its own process; the status-persisted scope is the
    shared input across that boundary."""
    scope_topic_ids = _regenerate_scope_topic_ids(out_dir)
    if not scope_topic_ids:
        return proposal, wiki
    from lib.topics.proposals.core_io import load_proposal

    try:
        prior = load_proposal(repo, out_dir.name)
    except Exception:  # noqa: BLE001 — best-effort; fall back to full redraft
        return proposal, wiki
    if not prior or not (prior.get("topics") or []):
        return proposal, wiki
    prior_wiki_path = out_dir / "wiki.md"
    prior_wiki = prior_wiki_path.read_text() if prior_wiki_path.exists() else ""
    return _splice_scoped_topics(prior, proposal, prior_wiki, scope_topic_ids)


def _topic_claimed_paths(topic: dict[str, Any]) -> set[str]:
    paths = set(path for path in topic.get("evidence_paths", []) if isinstance(path, str))
    for ref in topic.get("refs", []):
        if isinstance(ref, dict) and isinstance(ref.get("path"), str):
            paths.add(ref["path"])
    return paths


def _validate_paths(repo: Path, proposal: dict[str, Any]) -> None:
    # No evidence pack: every proposed path must exist in the working tree
    # and stay inside the repo. Stricter than the old evidence-or-repo check.
    repo_root = repo.resolve()
    for topic in proposal.get("topics", []):
        for path in _topic_claimed_paths(topic):
            resolved = (repo / path).resolve()
            if repo_root not in resolved.parents and resolved != repo_root:
                raise ValueError(f"path escapes repository: {path}")
            if not resolved.exists():
                raise ValueError(f"path not found in repository: {path}")


def _read_topic_signature(repo: Path) -> str | None:
    # Structural fingerprint of the approved topic graph that ignores the
    # `updated_at` field (rewritten on every save_graph) and any whitespace
    # / key-order changes. Only flips when a topic, edge, or alias actually
    # changed — so a concurrent no-op save no longer trips the integrity check.
    # Reads through load_graph so both disk layouts (legacy topic.json and
    # the per-topic split dir) are fingerprinted.
    from lib.topics.core import load_graph
    try:
        graph = load_graph(repo)
    except (TopicGraphError, OSError, json.JSONDecodeError):
        return None
    if isinstance(graph, dict):
        graph = {k: v for k, v in graph.items() if k != "updated_at"}
    return json.dumps(graph, sort_keys=True, separators=(",", ":"))


def _looks_like_permission_prompt(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "do you want to allow",
        "requires approval",
        "permission request",
        "approve this command",
        "approve the write prompt",
        "write permission prompt",
        "artifact file was not written",
        "blocked only on permission",
        "waiting for permission",
    )
    return any(marker in lowered for marker in markers)
