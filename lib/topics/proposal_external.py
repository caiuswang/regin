"""External agent runner for topic proposal generation."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lib.trace import trace_service
from lib.settings import settings
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.proposal_drafting import format_review_feedback_for_prompt
from lib.topics import TopicGraphError, topic_path, utc_now


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


def _repo_and_id_from_out_dir(out_dir: Path) -> tuple[Path, str]:
    """Derive (repo_path, proposal_id) from an out_dir.

    out_dir is always `<repo>/.regin/topics/proposals/<id>` — this
    convention has been load-bearing since the proposal feature
    shipped and is locked in by `lib.topics.core.topic_dir`.
    """
    proposal_id = out_dir.name
    repo_path = out_dir.parents[3]  # proposals/ → topics/ → .regin/ → repo
    return repo_path, proposal_id


def write_status(out_dir: Path, status: dict[str, Any]) -> dict[str, Any]:
    """Dual-write proposal status: ORM (authoritative) + disk status.json.

    Phase E2 added the ORM write. The disk file stays for tests that
    inspect it directly between operations. Drop the disk write in
    E3/follow-up once the test surface migrates.
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
    metadata_patch = {
        k: v for k, v in status.items()
        if k not in column_fields and k not in {"agent", "updated_at"}
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


def _existing_topics_summary(repo: Path) -> list[dict[str, Any]]:
    """Existing approved topics (id/label/aliases) so the agent avoids
    proposing duplicates.

    The agent has Read/Glob/Grep tools and explores the repo itself, so
    this is the only context we pre-derive — there is no evidence pack.
    """
    try:
        graph = load_authoritative_graph(repo)
    except Exception:
        return []
    return [
        {"id": tid, "label": topic.get("label"), "aliases": topic.get("aliases", [])}
        for tid, topic in sorted((graph.get("topics") or {}).items())
    ]


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
    """Resolve the agent id + its configured launch spec, or raise ValueError."""
    agent = agent_id or default_external_agent_id()
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
    try:
        stdout, stderr = proc.communicate(instructions, timeout=config.timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        stdout, stderr = proc.communicate()
        stdout_path.write_text(stdout or "")
        stderr_path.write_text(stderr or "")
        return _fail(
            out_dir,
            trace_id,
            status,
            "timed_out",
            f"external agent timed out after {config.timeout_seconds}s",
            exc,
            stdout=stdout,
            stderr=stderr,
        )
    return _handle_agent_output(ctx, proc, stdout, stderr, status)


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
        return _fail(ctx.out_dir, ctx.trace_id, status, "failed", f"external agent output invalid: {exc}", exc)

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
    write_status(ctx.out_dir, status)
    _emit(ctx.trace_id, "proposal.agent.complete", {"proposal_id": ctx.proposal_id, "agent": ctx.agent, "duration_ms": duration_ms}, status_code="OK")
    _emit_session_end(ctx.trace_id, reason="completed")
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
) -> tuple[dict[str, Any], str]:
    diagnostics = _failure_diagnostics(stdout=stdout, stderr=stderr)
    if diagnostics.summary:
        message = f"{message}: {diagnostics.summary}"
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


def _instructions(
    repo: Path,
    topic_request: str | None,
    out_dir: Path,
    temp_output_path: Path,
    *,
    prior_draft: dict[str, Any] | None = None,
    prompt_templates: list[dict[str, Any]] | None = None,
) -> str:
    prior_reference = ""
    if prior_draft:
        feedback_reference = ""
        feedback_block = format_review_feedback_for_prompt(prior_draft.get("feedback_threads") or [])
        if feedback_block:
            feedback_reference = f"""
{feedback_block}

"""
        prior_reference = f"""

Prior draft reference:
{feedback_reference}Use the previous proposal and wiki only as reference — to keep good coverage and to address any review feedback above — not as a baseline to diff against. Re-check every topic against the current repository.

Write the wiki and notes as a standalone description of the repository as it is NOW. Do NOT write changelog or diff prose comparing this revision to the previous one: avoid phrasing like "was removed", "is now", "no longer", "the old …", "previously", "changed from", or "renamed to". The reader has never seen the prior draft — describe the current structure and behavior directly, citing files that exist today.

Previous proposal JSON:
```json
{json.dumps(_prior_proposal_for_prompt(prior_draft.get('proposal')), indent=2, sort_keys=True)}
```

Previous wiki markdown:
```markdown
{str(prior_draft.get('wiki') or '')}
```
"""
    custom = _format_template_section(prompt_templates)
    return f"""# Regin Topic Proposal Agent Task

Inspect this repository as needed and draft reviewable topic graph proposals.

User topic request:
{topic_request or "No specific topic request was provided. Propose the most useful uncovered topics from the repository."}{prior_reference}{custom}

Rules:
- Do not modify `.regin/topics/topic.json` or approved topic data.
- Write final JSON to the temp output file `{temp_output_path}`.
- Do not write `{out_dir / OUTPUT_FILE}` directly; regin will validate and copy the temp output into that canonical artifact.
- You may also print the same JSON as a fenced `json` block.
- Keep all file paths relative to the repository root.
- Only propose topics justified by real repository files; every ref path must exist in the repo (regin rejects paths it can't find on disk).
- A ref's `role` is optional; when you set one, use only: overview, architecture, entrypoint, api, schema, test, migration, implementation, config, docs. Omit it if none clearly fits.
- `aliases` are *alternate* phrases a future agent might search for — not restatements of the `id` or `label`. Do NOT list the topic id or label, and do NOT add variants that differ only in case, spacing, or hyphenation: regin normalizes aliases (lowercased, every run of non-alphanumeric characters → a single space), so `foo-bar`, `Foo Bar`, and `foo bar` all collapse to the same key and a repeat is rejected at apply time. Give 0–6 genuinely distinct phrasings, or leave the list empty.
- `parent_id` places the topic under one top-level navigation bucket (see "Available buckets" below). Pick the single best-fitting bucket id. If none clearly fits, set it to `null` — the reviewer will place it; do NOT force a weak fit. `blurb` is a one-line router card ("what task should drill in here"), not a description; omit it and `intent` is used instead.
- If a write/tool permission prompt blocks writing the output file, stop and report the permission failure instead of printing a fallback success payload.

Output JSON shape:
{{
  "topics": [
    {{
      "id": "short-stable-id",
      "label": "Human label",
      "aliases": [],
      "intent": "What this topic helps future agents understand",
      "status": "active",
      "parent_id": "one-of-the-bucket-ids-below-or-null",
      "blurb": "One line: what task should drill into this topic",
      "refs": [{{"path": "relative/path.py", "role": "implementation"}}],
      "edges": [],
      "commands": [],
      "include_globs": ["path/**"],
      "exclude_globs": [],
      "evidence_paths": ["relative/path.py"]
    }}
  ],
  "notes": [],
  "wiki": "Markdown wiki draft"
}}

Existing approved topics (do not propose duplicates; explore the repo with your Read/Glob/Grep tools for everything else):
```json
{json.dumps(_existing_topics_summary(repo), indent=2, sort_keys=True)}
```

Available buckets (pick one id for each topic's `parent_id`, or null if none fits):
```json
{json.dumps(_bucket_summary(repo), indent=2, sort_keys=True)}
```
"""


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


def _normalise_agent_payload(repo: Path, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    from lib.topics.proposal_drafting import PROPOSAL_VERSION, validate_proposal

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
    wiki = str(payload.get("wiki") or "")
    errors = validate_proposal(proposal)
    if errors:
        raise ValueError("; ".join(errors))
    if not wiki.strip():
        raise ValueError("external agent returned an empty wiki")
    return proposal, wiki


def _validate_paths(repo: Path, proposal: dict[str, Any]) -> None:
    # No evidence pack: every proposed path must exist in the working tree
    # and stay inside the repo. Stricter than the old evidence-or-repo check.
    repo_root = repo.resolve()
    for topic in proposal.get("topics", []):
        paths = set(path for path in topic.get("evidence_paths", []) if isinstance(path, str))
        for ref in topic.get("refs", []):
            if isinstance(ref, dict) and isinstance(ref.get("path"), str):
                paths.add(ref["path"])
        for path in paths:
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
    path = topic_path(repo)
    if not path.exists():
        return None
    try:
        graph = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
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
