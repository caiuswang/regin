"""Capture Claude Code dynamic-workflow runs as regin session/span trees.

A *dynamic workflow* (the Claude Code `Workflow` tool) runs many subagents
in a background runtime across named *phases*. Its agents never reach
regin's hooks, but the runtime persists the whole run on disk. This module
reads those artifacts and projects each run onto regin's existing
OTel-style span store (``sessions`` + ``session_spans``) with **zero new
schema**, so a workflow run renders in the normal trace UI as
``run -> phase -> agent -> turn``.

On-disk layout, per run ``wf_<id>`` inside a Claude session dir ``<S>``::

    <S>/workflows/wf_<id>.json                 run manifest (written at completion)
    <S>/workflows/scripts/<name>-wf_<id>.js    the script (written at start)
    <S>/subagents/workflows/wf_<id>/
        journal.jsonl                          started/result events (written live)
        agent-<agentId>.jsonl  + .meta.json    each agent's transcript

The rich manifest only exists once a run is terminal, so capture has two
stages (see `ingest_run`):

* **live** (no manifest yet) -> `build_flat_spans`: a flat run + agent list,
  status ``active``, per-agent token totals from the live transcripts.
* **completed** (manifest present) -> `build_full_spans`: the full
  ``phase -> agent -> turn`` tree, status ``ended``, with each agent's
  transcript expanded into per-turn / per-tool spans.

Span ids are deterministic (``wfrun-`` / ``wfphase-`` / ``wfagent-`` /
``wfturn-`` / ``wftool-``), so re-ingesting a run is idempotent. `reingest`
clears a run's rows before re-inserting, so the live flat tree is cleanly
replaced by the full tree on completion.
"""

from __future__ import annotations

import difflib
import glob
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lib.activity_log import get_activity_logger
from lib.providers import get_active_provider

log = get_activity_logger("trace_ingest")

_WF_PREFIX = "wf_"
_PREVIEW_MAX = 240
# read_usage stores per-turn response text; we only need token + tool
# structure, so cap stored text small to bound memory on big transcripts.
_TURN_TEXT_CAP = 4000


@dataclass(frozen=True)
class RunRef:
    """Filesystem handle to one workflow run, live or completed."""

    run_id: str
    session_dir: Path
    journal_path: Path
    agents_dir: Path
    manifest_path: Path
    script_path: Path | None

    @property
    def terminal(self) -> bool:
        """A run is terminal once its manifest has been written."""
        return self.manifest_path.exists()

    def state_mtime(self) -> float:
        """mtime of the file defining the run's current state.

        The manifest once terminal (stable after completion), else the
        live journal (grows as agents start/finish). Drives the watcher's
        re-ingest gate.
        """
        p = self.manifest_path if self.terminal else self.journal_path
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def discover_runs(projects_dir: str | os.PathLike | None = None) -> list[RunRef]:
    """Find every workflow run under the provider's transcript projects dir.

    Anchored on ``journal.jsonl`` (written at run start) so live and
    completed runs are both found. Reuses
    `lib.providers.base.AgentProvider.transcript_projects_dir`.
    """
    if projects_dir is None:
        projects_dir = str(get_active_provider().transcript_projects_dir())
    # projects/<project>/<session>/subagents/workflows/<run_id>/journal.jsonl
    pattern = os.path.join(
        str(projects_dir), "*", "*", "subagents", "workflows",
        f"{_WF_PREFIX}*", "journal.jsonl",
    )
    refs: list[RunRef] = []
    for journal in glob.glob(pattern):
        agents_dir = Path(journal).parent
        run_id = agents_dir.name
        # <S>/subagents/workflows/<run_id> -> <S>
        session_dir = agents_dir.parent.parent.parent
        manifest_path = session_dir / "workflows" / f"{run_id}.json"
        scripts = glob.glob(
            str(session_dir / "workflows" / "scripts" / f"*-{run_id}.js")
        )
        refs.append(RunRef(
            run_id=run_id,
            session_dir=session_dir,
            journal_path=Path(journal),
            agents_dir=agents_dir,
            manifest_path=manifest_path,
            script_path=Path(scripts[0]) if scripts else None,
        ))
    return refs


# --------------------------------------------------------------------------
# small parse / format helpers
# --------------------------------------------------------------------------

def _iso(ms: int | float | None) -> str | None:
    """Epoch milliseconds -> UTC ISO 8601 string (None-safe)."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def _preview(value: object) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value)
    return text[:_PREVIEW_MAX]


def _parse_script_meta(script_path: Path | None) -> tuple[str | None, str | None]:
    """Best-effort ``name`` / ``description`` from a run's persisted script.

    The script is written at run start, so it gives a non-blank title for
    a live run before the manifest (which carries ``summary``) exists.
    """
    if script_path is None or not script_path.exists():
        return None, None
    try:
        head = script_path.read_text(encoding="utf-8")[:2000]
    except OSError:
        return None, None
    name = re.search(r"name:\s*['\"]([^'\"]+)['\"]", head)
    desc = re.search(r"description:\s*['\"]([^'\"]+)['\"]", head)
    return (name.group(1) if name else None,
            desc.group(1) if desc else None)


def _first_agent_value(agents_dir: Path, agent_ids: list[str], key: str) -> str | None:
    """Read ``key`` from the first line of the first available agent jsonl.

    Used for ``cwd`` (present on every transcript's first user entry) so
    the run's session row gets tagged to its repo via the normal ingest
    path, and for an accurate live start timestamp.
    """
    for agent_id in agent_ids:
        path = agents_dir / f"agent-{agent_id}.jsonl"
        try:
            with open(path, encoding="utf-8") as fh:
                first = fh.readline().strip()
        except OSError:
            continue
        if not first:
            continue
        try:
            entry = json.loads(first)
        except ValueError:
            continue
        val = entry.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


# --------------------------------------------------------------------------
# span construction
# --------------------------------------------------------------------------

def _span(trace_id: str, span_id: str, name: str, *, parent_id: str | None = None,
          start_time: str | None = None, end_time: str | None = None,
          duration_ms: int = 0, status_code: str = "OK",
          attrs: dict | None = None, is_test: bool = False) -> dict:
    """One OTel-style span dict in the shape `ingest_session_spans` expects."""
    a = {k: v for k, v in (attrs or {}).items() if v is not None}
    if is_test:
        a["is_test"] = True
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_id": parent_id,
        "name": name,
        "kind": "internal",
        "start_time": start_time,
        "end_time": end_time if end_time is not None else start_time,
        "duration_ms": duration_ms,
        "attributes": a,
        "status_code": status_code,
        "status_message": None,
    }


def _tool_inputs_by_id(path: Path) -> dict:
    """Map tool_use_id -> raw input dict by scanning a transcript's assistant
    content blocks. read_usage only retains tool inputs on errors, so the
    label-driving attrs (command, file_path, ...) come from this scan."""
    out: dict = {}
    for entry in _read_jsonl(path):
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                out[block["id"]] = block.get("input") or {}
    return out


_BASH_OUTPUT_CAP = 12000
_BASH_CMD_CAP = 4000


def _result_block_text(inner: object) -> str:
    """A tool_result block's content is either a plain string or a list of
    content parts; collapse both to text."""
    if isinstance(inner, str):
        return inner
    if isinstance(inner, list):
        return "\n".join(b.get("text", "") for b in inner
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _tool_outputs_by_id(path: Path) -> dict:
    """Map tool_use_id -> (output_text, is_error) from a transcript's
    tool_result blocks. Source of Bash stdout/stderr (the manifest/journal
    carry no tool output)."""
    out: dict = {}
    for entry in _read_jsonl(path):
        if entry.get("type") != "user":
            continue
        content = entry.get("message", {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id"):
                out[block["tool_use_id"]] = (
                    _result_block_text(block.get("content")), bool(block.get("is_error")))
    return out


def _count_diff_lines(diff: list[str]) -> tuple[int, int]:
    added = removed = 0
    for d in diff:
        if d.startswith(("+++", "---")):
            continue
        if d.startswith("+"):
            added += 1
        elif d.startswith("-"):
            removed += 1
    return added, removed


def _unified_diff(old: str, new: str) -> tuple[str, int, int]:
    """Standard unified diff of two strings + added/removed line counts."""
    diff = list(difflib.unified_diff(
        (old or "").splitlines(), (new or "").splitlines(), lineterm="", n=2))
    added, removed = _count_diff_lines(diff)
    body = "\n".join(d for d in diff if not d.startswith(("---", "+++")))
    return body[:6000], added, removed


def _attach_diff(attrs: dict, name: str, tinput: dict) -> None:
    """Populate diff/edit_op/added_lines/removed_lines for edit-family tools,
    from the tool input alone (no tool_response needed)."""
    if name == "Write":
        old, new = "", tinput.get("content") or ""
    elif name == "MultiEdit":
        edits = tinput.get("edits") or []
        old = "\n".join(e.get("old_string", "") for e in edits)
        new = "\n".join(e.get("new_string", "") for e in edits)
    else:  # Edit
        old, new = tinput.get("old_string") or "", tinput.get("new_string") or ""
    diff, added, removed = _unified_diff(old, new)
    if diff:
        attrs.update(diff=diff, edit_op=name.lower(),
                     added_lines=added, removed_lines=removed)


def _tool_attrs(agent_id: str, name: str, tinput: dict) -> dict:
    """Rebuild the label/render attributes a tool span needs from its input
    (what post_tool_trace computes live for hook-sourced sessions)."""
    attrs: dict = {"agent_id": agent_id, "tool_name": name, "tool_input": tinput}
    if tinput.get("command"):
        cmd = str(tinput["command"])
        attrs["command_preview"] = cmd[:200]
        attrs["command"] = cmd[:_BASH_CMD_CAP]
    fp = tinput.get("file_path") or tinput.get("path") or tinput.get("notebook_path")
    if fp:
        attrs["file_path"] = fp
    if tinput.get("query"):
        attrs["query"] = tinput["query"]
    if tinput.get("pattern"):
        attrs["pattern"] = tinput["pattern"]
    if name in ("Edit", "Write", "MultiEdit"):
        _attach_diff(attrs, name, tinput)
    return attrs


def _attach_bash_output(attrs: dict, name: str, output) -> None:
    """Attach Bash stdout/stderr from its tool_result. The transcript gives
    one combined output string, so it lands in stdout (or stderr on error)."""
    if name != "Bash" or not output:
        return
    text, is_err = output
    if not text:
        return
    body, dropped = text[:_BASH_OUTPUT_CAP], max(0, len(text) - _BASH_OUTPUT_CAP)
    key = "stderr" if is_err else "stdout"
    attrs[key] = body
    if dropped:
        attrs[f"{key}_truncated_bytes"] = dropped


def _tool_spans(run_id: str, parent_id: str, agent_id: str, turn_idx: int,
                ts: str | None, tool_calls, tool_inputs: dict,
                tool_outputs: dict, is_test: bool) -> list[dict]:
    """``tool.<name>`` spans for one turn, with full label/render attributes.

    Skips ``StructuredOutput`` — that call *is* the agent's result, surfaced
    by the agent's RESULT card rather than as a giant raw tool row.
    """
    out: list[dict] = []
    for jdx, call in enumerate(tool_calls or ()):
        tname = call.get("name") or "tool"
        if tname == "StructuredOutput":
            continue
        attrs = _tool_attrs(agent_id, tname, tool_inputs.get(call.get("id")) or {})
        _enrich_call_attrs(attrs, call)
        _attach_bash_output(attrs, tname, tool_outputs.get(call.get("id")))
        out.append(_span(
            run_id, f"wftool-{run_id}-{agent_id}-{turn_idx}-{jdx}",
            f"tool.{tname}", parent_id=parent_id, start_time=ts, end_time=ts,
            status_code="ERROR" if call.get("is_error") else "OK",
            attrs=attrs, is_test=is_test,
        ))
    return out


def _enrich_call_attrs(attrs: dict, call: dict) -> None:
    """Carry per-call flags (error, server-side advisor reply) onto the span."""
    if call.get("is_error"):
        attrs["is_error"] = True
    if call.get("server_side"):
        attrs["server_side"] = True
    if call.get("response_text"):  # advisor / web tools carry their reply
        attrs["response_text"] = call["response_text"]
    if call.get("advisor_model"):
        attrs["advisor_model"] = call["advisor_model"]


def _agent_turn_spans(run_id: str, agent_span_id: str, agent_id: str,
                      agents_dir: Path, is_test: bool) -> list[dict]:
    """Expand one agent's transcript into per-turn + per-tool spans.

    Reuses `lib.trace.transcript_usage.read_usage`. ``assistant_response``
    spans carry ``output_tokens`` in their attributes, which the ingest
    INSERT promotes to the column the trace tree renders.
    """
    from lib.trace.transcript_usage import read_usage

    path = agents_dir / f"agent-{agent_id}.jsonl"
    if not path.exists():
        return []
    usage = read_usage(str(path), max_text_bytes=_TURN_TEXT_CAP)
    if usage is None or not usage.turns:
        return []
    tool_inputs = _tool_inputs_by_id(path)
    tool_outputs = _tool_outputs_by_id(path)
    spans: list[dict] = []
    for idx, turn in enumerate(usage.turns):
        ts = turn.timestamp
        resp_id = f"wfturn-{run_id}-{agent_id}-{idx}"
        # A turn emits a response card when it produced text, else a thinking
        # card when it only reasoned; tools nest under whichever exists (or the
        # agent itself), avoiding an empty bubble / orphan parent_id.
        head = _turn_head_span(run_id, resp_id, agent_span_id, agent_id, turn, ts, is_test)
        if head is not None:
            spans.append(head)
        tool_parent = resp_id if head is not None else agent_span_id
        spans.extend(_tool_spans(run_id, tool_parent, agent_id, idx, ts,
                                 turn.tool_calls, tool_inputs, tool_outputs, is_test))
    return spans


def _turn_head_span(run_id: str, resp_id: str, parent_id: str, agent_id: str,
                    turn, ts: str | None, is_test: bool) -> dict | None:
    """The per-turn head: an ``assistant_response`` (when the turn has text,
    carrying any thinking) or an ``assistant.thinking`` card (thinking only).
    None for a pure tool-call turn."""
    base = {"agent_id": agent_id, "output_tokens": turn.output_tokens,
            "input_tokens": turn.input_tokens, "model": turn.model,
            "turn_uuid": turn.uuid}
    if turn.thinking_text:
        base["thinking_text"] = turn.thinking_text
        base["thinking_truncated"] = turn.thinking_text_truncated
    if turn.text:
        return _span(run_id, resp_id, "assistant_response", parent_id=parent_id,
                     start_time=ts, end_time=ts,
                     duration_ms=turn.inference_duration_ms or 0,
                     attrs={**base, "text": turn.text, "truncated": turn.text_truncated},
                     is_test=is_test)
    if turn.thinking_text:
        return _span(run_id, resp_id, "assistant.thinking", parent_id=parent_id,
                     start_time=ts, end_time=ts,
                     duration_ms=turn.inference_duration_ms or 0,
                     attrs=base, is_test=is_test)
    return None


def _root_and_title_spans(run_id: str, *, title: str, start: str | None,
                          end: str | None, attrs: dict, is_test: bool) -> list[dict]:
    """The run-root ``session.start`` span (carries agent_type/model/cwd that
    drive the session row) plus the ``prompt`` span that becomes its title."""
    root_id = f"wfrun-{run_id}"
    return [
        _span(run_id, root_id, "session.start", start_time=start, end_time=end,
              attrs=attrs, is_test=is_test),
        _span(run_id, f"wfprompt-{run_id}", "prompt", parent_id=root_id,
              start_time=start, end_time=start,
              attrs={"text": title}, is_test=is_test),
    ]


def _run_bounds(manifest: dict) -> tuple[str | None, str | None]:
    """(start, end) ISO strings from the manifest's epoch-ms start+duration."""
    start_ms = manifest.get("startTime")
    dur = manifest.get("durationMs") or 0
    end_ms = start_ms + dur if start_ms else None
    return _iso(start_ms), _iso(end_ms)


def _manifest_agents(manifest: dict) -> tuple[list[dict], list[str]]:
    """The ``workflow_agent`` progress entries and their agent ids."""
    progress = manifest.get("workflowProgress") or []
    agents = [e for e in progress if e.get("type") == "workflow_agent"]
    return agents, [a["agentId"] for a in agents if a.get("agentId")]


def _phase_spans(run_id: str, root_id: str, manifest: dict, start: str | None,
                 end: str | None, is_test: bool) -> tuple[list[dict], dict[int, str]]:
    """One ``workflow.phase`` span per declared phase, plus an index map so
    agents can be parented to their phase."""
    spans: list[dict] = []
    phase_ids: dict[int, str] = {}
    for i, phase in enumerate(manifest.get("phases") or [], start=1):
        pid = f"wfphase-{run_id}-{i}"
        phase_ids[i] = pid
        spans.append(_span(run_id, pid, "workflow.phase", parent_id=root_id,
                           start_time=start, end_time=end,
                           attrs={"title": phase.get("title"),
                                  "detail": phase.get("detail"), "index": i},
                           is_test=is_test))
    return spans, phase_ids


def build_full_spans(manifest: dict, agents_dir: Path, *, deep: bool = True,
                     is_test: bool = False) -> list[dict]:
    """Build the complete span tree for a *terminal* run from its manifest."""
    run_id = manifest["runId"]
    root_id = f"wfrun-{run_id}"
    start, end = _run_bounds(manifest)
    status = manifest.get("status") or "completed"
    agents, agent_ids = _manifest_agents(manifest)
    title = manifest.get("summary") or manifest.get("workflowName") or run_id

    spans = _root_and_title_spans(
        run_id, title=title, start=start, end=end,
        attrs={"agent_type": "workflow", "model": manifest.get("defaultModel"),
               "cwd": _first_agent_value(agents_dir, agent_ids, "cwd"),
               "run_id": run_id, "task_id": manifest.get("taskId"),
               "workflow_name": manifest.get("workflowName"),
               "workflow_status": status, "agent_count": manifest.get("agentCount"),
               "total_tokens": manifest.get("totalTokens"),
               "total_tool_calls": manifest.get("totalToolCalls")},
        is_test=is_test)

    phases, phase_ids = _phase_spans(run_id, root_id, manifest, start, end, is_test)
    spans.extend(phases)
    # Full per-agent results live in the journal (the manifest only previews).
    _, results = _journal_agents(_read_jsonl(agents_dir / "journal.jsonl"))
    for agent in agents:
        spans.extend(_full_agent_spans(
            run_id, root_id, phase_ids, agent, agents_dir, results, deep, is_test))

    spans.append(_span(run_id, f"wfend-{run_id}", "session.end", parent_id=root_id,
                       start_time=end, end_time=end,
                       status_code="OK" if status == "completed" else "ERROR",
                       attrs={"reason": status}, is_test=is_test))
    return spans


def _agent_full_prompt(agents_dir: Path, agent_id: str) -> str | None:
    """The agent's full dispatched prompt = its transcript's first user
    message (the manifest only carries a ~400-char preview)."""
    for entry in _read_jsonl(agents_dir / f"agent-{agent_id}.jsonl"):
        if entry.get("type") != "user":
            continue
        content = entry.get("message", {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text = "\n".join(b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text")
            return text or None
        return None
    return None


def _result_text(result: object) -> str | None:
    """Render an agent's result for display: structured results (schema'd
    agents) pretty-print as JSON; text results pass through."""
    if result is None:
        return None
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(result)


def _full_agent_spans(run_id: str, root_id: str, phase_ids: dict[int, str],
                      agent: dict, agents_dir: Path, results: dict, deep: bool,
                      is_test: bool) -> list[dict]:
    """One agent's ``subagent.start`` span plus its deep turn children.

    Prompt + result are sourced from the transcript / journal (full text),
    not the manifest's truncated previews, so "Show full prompt" / the RESULT
    card show the complete content.
    """
    agent_id = agent.get("agentId")
    parent = phase_ids.get(agent.get("phaseIndex"), root_id)
    started = agent.get("startedAt")
    dur = agent.get("durationMs") or 0
    span_id = f"wfagent-{run_id}-{agent_id}"
    full_prompt = (_agent_full_prompt(agents_dir, agent_id) if agent_id else None) \
        or agent.get("promptPreview")
    result_full = _result_text(results.get(agent_id)) or agent.get("resultPreview")
    spans = [_span(
        run_id, span_id, "subagent.start", parent_id=parent,
        start_time=_iso(started), end_time=_iso(started + dur) if started else None,
        duration_ms=dur,
        attrs={"agent_id": agent_id, "agent_type": agent.get("agentType"),
               "agent_name": agent.get("label"), "label": agent.get("label"),
               "model": agent.get("model"), "state": agent.get("state"),
               "phase_title": agent.get("phaseTitle"),
               "prompt": full_prompt,
               "result_full": result_full,
               "result_preview": _preview(result_full),
               "tokens": agent.get("tokens"), "tool_calls": agent.get("toolCalls")},
        is_test=is_test)]
    if deep and agent_id:
        spans.extend(_agent_turn_spans(run_id, span_id, agent_id, agents_dir, is_test))
    return spans


def _journal_agents(events: list[dict]) -> tuple[list[str], dict[str, object]]:
    """(started agent ids in order, {agentId: result}) from journal events."""
    started: list[str] = []
    results: dict[str, object] = {}
    for e in events:
        agent_id = e.get("agentId")
        if not agent_id:
            continue
        if e.get("type") == "started":
            started.append(agent_id)
        elif e.get("type") == "result":
            results[agent_id] = e.get("result")
    return started, results


def _flat_agent_span(run_ref: RunRef, root_id: str, agent_id: str,
                     results: dict, start: str | None, is_test: bool) -> dict:
    """One agent's live ``subagent.start`` span with its current token total."""
    from lib.trace.transcript_usage import read_usage

    path = run_ref.agents_dir / f"agent-{agent_id}.jsonl"
    usage = read_usage(str(path), max_text_bytes=0) if path.exists() else None
    meta = _read_json(run_ref.agents_dir / f"agent-{agent_id}.meta.json") or {}
    return _span(
        run_ref.run_id, f"wfagent-{run_ref.run_id}-{agent_id}", "subagent.start",
        parent_id=root_id, start_time=start,
        attrs={"agent_id": agent_id, "agent_type": meta.get("agentType"),
               "state": "done" if agent_id in results else "running",
               "result_preview": _preview(results.get(agent_id)),
               "tokens": usage.output_tokens if usage else None},
        is_test=is_test)


def build_flat_spans(run_ref: RunRef, *, is_test: bool = False) -> list[dict]:
    """Build the coarse, live tree for an *in-progress* run from the journal.

    No manifest exists yet, so there are no phases: agents hang directly
    off the run root, marked running/done. Per-agent token totals come from
    the live transcripts (`read_usage`).
    """
    run_id = run_ref.run_id
    root_id = f"wfrun-{run_id}"
    started, results = _journal_agents(_read_jsonl(run_ref.journal_path))
    name, desc = _parse_script_meta(run_ref.script_path)
    start = _first_agent_value(run_ref.agents_dir, started, "timestamp") \
        or _iso(int(run_ref.state_mtime() * 1000))

    spans = _root_and_title_spans(
        run_id, title=(desc or name or run_id), start=start, end=None,
        attrs={"agent_type": "workflow", "workflow_name": name, "run_id": run_id,
               "workflow_status": "running",
               "cwd": _first_agent_value(run_ref.agents_dir, started, "cwd"),
               "agent_count": len(started)},
        is_test=is_test)
    for agent_id in started:
        spans.append(_flat_agent_span(run_ref, root_id, agent_id, results,
                                      start, is_test))
    return spans


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def _clear_run(run_id: str) -> None:
    """Delete a run's existing rows so re-ingest can't double-count or leave
    stale tree-map rows (the live flat tree is replaced by the full tree)."""
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in ("session_spans", "session_trace_map", "sessions"):
            conn.execute(f"DELETE FROM {table} WHERE trace_id = ?", (run_id,))
        conn.commit()
    finally:
        conn.close()


def _set_session_tokens(run_id: str, output_tokens: int | None) -> None:
    """Stamp the run's headline token total onto the session row.

    The normal per-turn aggregator (`turn_trace`) never runs for workflow
    runs, so the Sessions list / summary would show no tokens without this.
    ``peak_context_tokens`` is left NULL — a fan-out has no single context
    window, so context% is intentionally blank.
    """
    if not output_tokens:
        return
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        conn.execute("UPDATE sessions SET output_tokens = ? WHERE trace_id = ?",
                     (int(output_tokens), run_id))
        conn.commit()
    finally:
        conn.close()


def reingest(run_id: str, spans: list[dict]) -> tuple[int, int]:
    """Clear then re-insert a run's spans via the shared ingest service.

    Returns ``(ingested, skipped)`` from
    `lib.trace.trace_service.ingest_session_spans`.
    """
    from lib.trace.trace_service import ingest_session_spans

    _clear_run(run_id)
    normalised = [(span, span["attributes"]) for span in spans]
    return ingest_session_spans(normalised)


def ingest_run(run_ref: RunRef, *, deep: bool = True,
               is_test: bool = False) -> tuple[int, int] | None:
    """Ingest one run: full tree if terminal, flat live tree otherwise."""
    if run_ref.terminal:
        manifest = _read_json(run_ref.manifest_path)
        if manifest is None or not manifest.get("runId"):
            return None
        spans = build_full_spans(manifest, run_ref.agents_dir,
                                 deep=deep, is_test=is_test)
        result = reingest(run_ref.run_id, spans)
        _set_session_tokens(run_ref.run_id, manifest.get("totalTokens"))
        return result
    spans = build_flat_spans(run_ref, is_test=is_test)
    result = reingest(run_ref.run_id, spans)
    total_out = sum(s["attributes"].get("tokens") or 0
                    for s in spans if s["name"] == "subagent.start")
    _set_session_tokens(run_ref.run_id, total_out or None)
    return result


def ingest_all(*, deep: bool = True) -> dict:
    """Ingest every discoverable run once. Returns a small summary dict."""
    summary = {"runs": 0, "spans": 0, "failed": 0}
    for run_ref in discover_runs():
        try:
            result = ingest_run(run_ref, deep=deep)
        except Exception:
            summary["failed"] += 1
            log.error("workflow_run_ingest_failed", exc_info=True,
                      run_id=run_ref.run_id)
            continue
        if result is not None:
            summary["runs"] += 1
            summary["spans"] += result[0]
            log.write("workflow_run_ingested", run_id=run_ref.run_id,
                      terminal=run_ref.terminal, spans=result[0])
    return summary


def watch(poll_seconds: float = 5.0, *, deep: bool = True, stop=None) -> None:
    """Poll for new/changed runs and (re)ingest them until ``stop`` is set.

    Re-ingest is gated on state mtime + terminal-ness so an unchanged run
    is skipped: a live run re-ingests (flat) only when its journal grows,
    and the one-time deep parse happens on the single completion pass.
    Intended to run in a daemon thread started by ``regin serve``.
    """
    seen: dict[str, tuple[float, bool]] = {}
    while stop is None or not stop.is_set():
        for run_ref in discover_runs():
            mtime = run_ref.state_mtime()
            if seen.get(run_ref.run_id) == (mtime, run_ref.terminal):
                continue
            try:
                ingest_run(run_ref, deep=deep)
                seen[run_ref.run_id] = (mtime, run_ref.terminal)
            except Exception:
                log.error("workflow_run_ingest_failed", exc_info=True,
                          run_id=run_ref.run_id)
        if stop is not None:
            stop.wait(poll_seconds)
        else:
            time.sleep(poll_seconds)
