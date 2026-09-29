"""External agent runner for topic proposal generation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lib.agent_bridge import child_env
from lib.trace import trace_service
from lib.settings import settings
from lib.topics import TopicGraphError, utc_now
from lib.topics.proposal_artifacts import (
    OUTPUT_FILE,
    PRIOR_PROPOSAL_FILE,
    STATUS_FILE,
    TEMP_OUTPUT_DIR,
    TEMP_OUTPUT_FILE,
    TOPIC_MAP_FILE,
    _apply_regenerate_scope,
    _combined_proposal_wiki,
    _load_agent_payload,
    _normalise_agent_payload,
    _pick_topic,
    _read_topic_signature,
    _regenerate_scope_topic_ids,
    _repo_and_id_from_out_dir,
    _splice_scoped_topics,
    _topic_wiki_section,
    _validate_paths,
    load_status,
    write_status,
)
from lib.topics.proposal_prompt import (
    _buckets_section,
    _existing_topics_pointer,
    _existing_topics_summary,
    _finish_command,
    _format_template_section,
    _instructions,
    _prior_proposal_for_prompt,
    _prior_proposal_pointer,
    _prior_reference_block,
    _scoped_refresh_directive,
    _sibling_refresh_section,
    _template_slugs,
    format_review_feedback_for_prompt,
)


FAILURE_SUMMARY_LIMIT = 500
FAILURE_DETAIL_LIMIT = 4000
FAILURE_STREAM_TAIL_LIMIT = 2000
PIPELINE_SESSION_TITLE = "Topic proposal pipeline (evidence → draft → review)"
# Agent commands the SDK tier can stand in for; anything else keeps its own
# binary, because the tier only ever launches `claude`.
_CLAUDE_COMMANDS = frozenset({"claude", "claude.exe"})


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
    # Known only when regin launched the agent itself; the subprocess path
    # has to go looking for the session the spawned CLI opened.
    agent_trace_id: str | None = None


@dataclass(frozen=True)
class _AgentExit:
    """How the drafting agent finished, in the terms the ingest path judges.

    The subprocess runner fills these from `communicate()`; the SDK runner has
    no captured streams (its transcript is spans) and reports the run's
    terminal state instead. One shape means the completion contract — finish
    signal first, then output validation, then the graph-integrity check — is
    the same code for both.
    """

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class _SdkRunProcess:
    """A `run_control`-shaped handle for a run owned by `lib/agent_sdk`.

    `run_control` holds `Popen`s so the Stop endpoint — a different request
    thread — can reach a run whose only other reference is a worker's stack.
    An SDK run has no child process of regin's own to signal: ending it means
    asking the supervisor to stop the session. Presenting the three members
    `run_control` actually uses keeps Stop, `is_live` and the stranded-run
    reaper working unchanged across both runners.
    """

    def __init__(self, handle):
        self._handle = handle
        self.pid = os.getpid()

    def poll(self) -> int | None:
        return 0 if self._handle.done() else None

    def terminate(self) -> None:
        self._handle.stop()


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
    use_sdk = _sdk_runner_enabled(config)

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
    _emit(trace_id, "proposal.agent.start", {
        "proposal_id": proposal_id, "agent": agent, "repo": str(repo),
        "runner": "agent-sdk" if use_sdk else "subprocess"})

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
    env_overlay = _agent_env_overlay(
        repo, out_dir, proposal_id, trace_id, temp_output_path, output_path)
    cwd = Path(config.cwd).expanduser() if config.cwd else repo
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
        started=time.monotonic(),
        prompt_templates=prompt_templates,
    )
    if use_sdk:
        return _run_via_agent_sdk(ctx, instructions, env_overlay, cwd, status,
                                  _sdk_run_options(config) or {})
    return _run_via_subprocess(
        ctx, instructions, child_env({**os.environ, **env_overlay}),
        [config.command, *config.args], cwd, status)


def _agent_env_overlay(
    repo: Path, out_dir: Path, proposal_id: str, trace_id: str,
    temp_output_path: Path, output_path: Path,
) -> dict[str, str]:
    """What the drafting agent's process learns about this run from its
    environment — the output/finish handshake, plus the surface tag.

    `REGIN_LLM_SURFACE` tags the agent's own Claude session by the drafting
    surface so ingest stamps origin='llm-stage' + the surface's declared tags
    on the REAL agent session (the synthetic wrapper's `agent_type` tag never
    reaches it). Mirrors proposal_review's REGIN_LLM_SURFACE.

    An overlay, not a full environment: the subprocess runner merges it over
    `os.environ` itself and the SDK does the same for its child.
    """
    from lib.prompts.surfaces.drafting import SURFACE_ID as _DRAFTING_SURFACE_ID
    return {
        "REGIN_LLM_SURFACE": _DRAFTING_SURFACE_ID,
        "REGIN_TOPIC_PROPOSAL_DIR": str(out_dir),
        "REGIN_TOPIC_PROPOSAL_OUTPUT": str(temp_output_path),
        "REGIN_TOPIC_PROPOSAL_CANONICAL_OUTPUT": str(output_path),
        "REGIN_TOPIC_PROPOSAL_TRACE_ID": trace_id,
        "REGIN_TOPIC_PROPOSAL_ID": proposal_id,
        "REGIN_TOPIC_PROPOSAL_FINISH_CMD": _finish_command(repo, proposal_id),
    }


def _run_via_subprocess(
    ctx: _AgentRunContext, instructions: str, env: dict[str, str],
    command: list[str], cwd: Path, status: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Spawn the configured agent command and block on it."""
    from lib.topics.proposals import run_control
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
        return _fail(ctx.out_dir, ctx.trace_id, status, "failed",
                     f"external agent failed to start: {exc}", exc)

    # Register the live process so the Stop endpoint (a different request
    # thread) can terminate it; happens before we block on communicate().
    run_control.register(ctx.proposal_id, proc)
    status["pid"] = proc.pid
    write_status(ctx.out_dir, status)
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
        ctx.stdout_path.write_text(stdout or "")
        ctx.stderr_path.write_text(stderr or "")
        return _timed_out(ctx, status, wait_timeout, exc,
                          stdout=stdout, stderr=stderr)
    return _handle_agent_output(
        ctx, _AgentExit(stdout, stderr, proc.returncode), status)


def _timed_out(
    ctx: _AgentRunContext, status: dict[str, Any], wait_timeout: int | None,
    exc: Exception, *, stdout: str | None = None, stderr: str | None = None,
) -> tuple[dict[str, Any], str]:
    """The ceiling fired. A run that already signalled is still a success.

    Re-notifying here would supersede the same inbox card but re-fire the push
    channels (record_message pushes on every write, supersede included),
    double-notifying Feishu/webhook — so the signalled branch returns the
    persisted result and emits nothing.
    """
    signaled = _load_signaled_result(ctx)
    if signaled is not None:
        return signaled
    return _fail(
        ctx.out_dir, ctx.trace_id, status, "timed_out",
        f"external agent timed out after {wait_timeout}s", exc,
        stdout=stdout, stderr=stderr,
    )


# CLI flags the SDK tier can honour as `RunOptions`, and the ones that are
# simply how a subprocess is driven and mean nothing to a client that already
# streams (`--print`, `-p`). Anything outside both sets is a flag the tier
# would drop on the floor, which is what `_sdk_args_honoured` refuses.
_ARGS_AS_OPTIONS = {"--model": "model", "--permission-mode": "permission_mode"}
_ARGS_IGNORABLE = {"--print", "-p"}


def _sdk_run_options(config: Any) -> dict[str, str] | None:
    """The configured agent's `args` as `RunOptions` fields, or None when they
    can't all be honoured.

    A configured agent is a *command line*: `claude-haiku` is the same binary
    as `claude-opus` and differs only by `--model haiku`. The tier launches
    through the SDK rather than that command line, so unless those flags are
    carried across, every configured agent silently collapses into the CLI
    default — and a dropped `--permission-mode bypassPermissions` puts an
    unattended drafting run into the mode where calls park.
    """
    args = list(getattr(config, "args", None) or [])
    resolved: dict[str, str] = {}
    index = 0
    while index < len(args):
        flag = args[index]
        if flag in _ARGS_IGNORABLE:
            index += 1
            continue
        field = _ARGS_AS_OPTIONS.get(flag)
        if field is None or index + 1 >= len(args):
            return None
        resolved[field] = args[index + 1]
        index += 2
    return resolved


def _sdk_runner_enabled(config: Any) -> bool:
    """Whether this run should go through the Agent SDK tier.

    Both gates have to be on, the configured agent has to be the CLI the tier
    can actually launch — `lib/agent_sdk` starts the user's own `claude`, so a
    drafting agent bound to codex/kimi, or to a wrapper regin can't recognise,
    keeps its own binary and its own subprocess — and its `args` have to be
    ones the tier can carry across. Falling back is always safe; silently
    running a different model than the operator configured is not.
    """
    if not settings.topic_evolution.proposal_sdk_runner:
        return False
    if not settings.agent_sdk.enabled:
        return False
    if Path(getattr(config, "command", "") or "").name not in _CLAUDE_COMMANDS:
        return False
    return _sdk_run_options(config) is not None


def _run_via_agent_sdk(
    ctx: _AgentRunContext, instructions: str, env: dict[str, str],
    cwd: Path, status: dict[str, Any], run_options: dict[str, str],
) -> tuple[dict[str, Any], str]:
    """Draft through `lib/agent_sdk` instead of a subprocess of our own.

    The completion contract is unchanged — the agent's `proposal-finish` call
    is still authoritative and the temp-file handshake is still the fallback —
    but the run is owned by the shared loop rather than by this thread's
    `Popen`, so it has a durable `agent_runs` row, live spans, and a `/live`
    session the operator can watch and stop while it works.
    """
    from lib.agent_sdk import supervisor
    from lib.topics.proposals import run_control

    try:
        handle = supervisor.launch_run(
            instructions, cwd=str(cwd), env=env, one_shot=True,
            # The configured agent's own flags win: `claude-haiku` differs
            # from `claude-opus` only by `--model`, so falling back to the
            # tier default here would run a model the operator did not pick.
            permission_mode=(run_options.get("permission_mode")
                             or settings.agent_sdk.permission_mode or "default"),
            model=run_options.get("model", ""))
    except Exception as exc:
        return _fail(ctx.out_dir, ctx.trace_id, status, "failed",
                     f"external agent failed to start: {exc}", exc)

    run_control.register(ctx.proposal_id, _SdkRunProcess(handle))
    status["pid"] = os.getpid()
    # Unlike a spawned CLI, a run regin owns names its own session up front —
    # no post-hoc search for the trace the agent happened to open.
    status["agent_trace_id"] = handle.trace_id
    status["agent_trace_url"] = f"/trace/sessions/{handle.trace_id}"
    write_status(ctx.out_dir, status)
    ctx = replace(ctx, agent_trace_id=handle.trace_id)

    wait_timeout = _proposal_wait_timeout()
    try:
        outcome = handle.wait(timeout=wait_timeout)
    except TimeoutError as exc:
        handle.stop()
        return _timed_out(ctx, status, wait_timeout, exc)
    return _handle_agent_output(ctx, _sdk_exit(outcome), status)


def _sdk_exit(outcome) -> _AgentExit:
    """Read an SDK run's terminal state as the exit the ingest path judges.

    A failed run has no captured streams to explain itself, so its detail
    stands in for stderr — that is what `_fail` summarises into the run's
    error message.
    """
    if outcome.status == "failed":
        return _AgentExit(stderr=outcome.detail, returncode=1)
    return _AgentExit()


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
    exit_: _AgentExit,
    status: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Post-run handling: cancellation, failures, payload parse.

    Split out of `run_external_agent_proposal` so the cancel check has one
    clear home and the parent stays under the complexity budget. The
    cancel check runs FIRST so a terminated agent (non-zero exit) is
    reported as `cancelled`, never `failed`.
    """
    from lib.topics.proposals import run_control
    stdout, stderr = exit_.stdout, exit_.stderr
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

    _reject_bad_agent_result(ctx, exit_, status)  # raises _fail on any problem

    agent_trace_id = ctx.agent_trace_id or _find_agent_session_trace_id(
        ctx.proposal_id, ctx.started)
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
    exit_: _AgentExit,
    status: dict[str, Any],
) -> None:
    """Raise via `_fail` if the agent hit a permission prompt, exited
    non-zero, or mutated the approved graph. Returns normally otherwise."""
    stdout, stderr = exit_.stdout, exit_.stderr
    if _looks_like_permission_prompt(f"{stdout}\n{stderr}"):
        _emit(ctx.trace_id, "proposal.agent.permission_request", {"proposal_id": ctx.proposal_id, "agent": ctx.agent})
        _fail(
            ctx.out_dir, ctx.trace_id, status, "waiting_for_permission",
            "external agent requested interactive permission; v1 runs non-interactively",
            stdout=stdout, stderr=stderr,
        )
    if exit_.returncode != 0:
        _fail(
            ctx.out_dir, ctx.trace_id, status, "failed",
            f"external agent exited with code {exit_.returncode}",
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
    # Local naive, the basis hook-ingested spans use: `sessions.last_seen` is
    # ordered as text, so a UTC `...Z` stamp sorts this run hours out of place.
    now = datetime.now().isoformat()
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
            "cwd": str(repo),
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
