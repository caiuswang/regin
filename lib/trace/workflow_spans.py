"""Projection of a workflow run onto regin spans (full manifest tree and flat
journal tree)."""

from __future__ import annotations

import difflib
import json
from datetime import datetime, timedelta
from pathlib import Path

from lib.trace.workflow_run_files import (
    RunRef,
    _TURN_TEXT_CAP,
    _first_agent_value,
    _iso,
    _iter_jsonl,
    _journal_agents,
    _manifest_agents,
    _parent_trace_id,
    _preview,
    _read_json,
    _read_jsonl,
)
from lib.trace.workflow_script_meta import (
    _parse_script_agents,
    _parse_script_meta,
    _parse_script_phases,
)


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

    Skips the *successful* ``StructuredOutput`` — that call *is* the agent's
    result, surfaced by the agent's RESULT card rather than as a giant raw
    tool row. A *failed* StructuredOutput is an error, not a result, so it's
    kept: the retries are exactly the story the workflow trace should show
    (mirrors the main session's `tool.failure` capture).
    """
    out: list[dict] = []
    for jdx, call in enumerate(tool_calls or ()):
        tname = call.get("name") or "tool"
        if tname == "StructuredOutput" and not call.get("is_error"):
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
        think_id = f"wfthink-{run_id}-{agent_id}-{idx}"
        spans.extend(_turn_head_spans(run_id, resp_id, think_id, agent_span_id,
                                      agent_id, turn, ts, is_test))
        tool_parent = (resp_id if turn.text
                       else think_id if turn.thinking_blocks
                       else agent_span_id)
        spans.extend(_tool_spans(run_id, tool_parent, agent_id, idx, ts,
                                 turn.tool_calls, tool_inputs, tool_outputs, is_test))
    return spans


def _stagger_before_ts(ts: str | None) -> str | None:
    """`ts` minus 1 ms so the thinking span sorts immediately ahead of its
    response in the start_time-ordered conversation tree (same idiom as
    span_posters._stagger_before). Returns `ts` unchanged when it isn't a
    parseable ISO timestamp."""
    if not ts:
        return ts
    try:
        base = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
        return (datetime.fromisoformat(base) - timedelta(milliseconds=1)).isoformat()
    except (ValueError, TypeError):
        return ts


def _turn_head_spans(run_id: str, resp_id: str, think_id: str, parent_id: str,
                     agent_id: str, turn, ts: str | None,
                     is_test: bool) -> list[dict]:
    """Per-turn head span(s): an ``assistant_response`` (text) and/or an
    ``assistant.thinking`` (reasoning). A turn with both emits BOTH — the
    thinking staggered 1 ms earlier so the start_time-ordered tree renders
    it first. Mirrors ``span_posters._maybe_emit_assistant_span``.

    Token split — this reconstruction does NOT attribute tokens to
    individual tool spans, so the head spans account for the whole turn:

      * no thinking          -> response carries the full turn
                                output_tokens (unchanged; tool_use stays
                                lumped with the text as before).
      * thinking present     -> response carries the text estimate and the
                                thinking span carries the remainder
                                (output - text) = reasoning + tool_use.

    Either way ``response + thinking == turn.output_tokens`` so nothing is
    lost. The prior code dropped an encrypted thinking-only turn entirely
    (no thinking_text -> no span) and folded thinking into the response
    bucket on text turns — both fixed here."""
    from lib.tokens.token_estimator import estimate_text_tokens  # type: ignore
    spans: list[dict] = []
    has_think = turn.thinking_blocks > 0
    text_out = estimate_text_tokens(turn.text) if turn.text else 0
    base = {"agent_id": agent_id, "input_tokens": turn.input_tokens,
            "model": turn.model, "turn_uuid": turn.uuid}
    if turn.text:
        resp_out = text_out if has_think else (turn.output_tokens or 0)
        spans.append(_span(
            run_id, resp_id, "assistant_response", parent_id=parent_id,
            start_time=ts, end_time=ts,
            duration_ms=turn.inference_duration_ms or 0,
            attrs={**base, "output_tokens": resp_out,
                   "text": turn.text, "truncated": turn.text_truncated},
            is_test=is_test))
    if has_think:
        spans.append(_thinking_head_span(
            run_id, think_id, parent_id, base, turn, ts, text_out,
            has_text=bool(turn.text), is_test=is_test))
    return spans


def _thinking_head_span(run_id: str, think_id: str, parent_id: str, base: dict,
                        turn, ts: str | None, text_out: int, *,
                        has_text: bool, is_test: bool) -> dict:
    """The turn's ``assistant.thinking`` span. Output is the turn's output
    minus the text estimate (reasoning + un-attributed tool_use); when the
    turn also has text it's staggered 1 ms ahead and carries no duration
    (the response span owns the per-call latency)."""
    tattrs = {**base, "output_tokens": max(0, (turn.output_tokens or 0) - text_out),
              "thinking_blocks": turn.thinking_blocks,
              "thinking_signature_bytes": turn.thinking_signature_bytes}
    if turn.thinking_text:
        tattrs["thinking_text"] = turn.thinking_text
        tattrs["thinking_truncated"] = turn.thinking_text_truncated
    think_ts = _stagger_before_ts(ts) if has_text else ts
    return _span(
        run_id, think_id, "assistant.thinking", parent_id=parent_id,
        start_time=think_ts, end_time=think_ts,
        duration_ms=0 if has_text else (turn.inference_duration_ms or 0),
        attrs=tattrs, is_test=is_test)


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


def _phase_spans(run_id: str, root_id: str, phases: list, start: str | None,
                 end: str | None, is_test: bool) -> tuple[list[dict], dict[int, str]]:
    """One ``workflow.phase`` span per declared phase, plus an index map so
    agents can be parented to their phase. Driven by a ``[{title, detail}]``
    list — the manifest's ``phases`` when terminal, or the script's declared
    ``meta.phases`` while live (so both paths build identical ``wfphase-`` ids
    and the live→complete re-ingest stays idempotent)."""
    spans: list[dict] = []
    phase_ids: dict[int, str] = {}
    for i, phase in enumerate(phases or [], start=1):
        pid = f"wfphase-{run_id}-{i}"
        phase_ids[i] = pid
        spans.append(_span(run_id, pid, "workflow.phase", parent_id=root_id,
                           start_time=start, end_time=end,
                           attrs={"title": phase.get("title"),
                                  "detail": phase.get("detail"), "index": i},
                           is_test=is_test))
    return spans, phase_ids


def build_full_spans(manifest: dict, agents_dir: Path, *, deep: bool = True,
                     is_test: bool = False,
                     snapshot_stale_at: str | None = None) -> list[dict]:
    """Build the full phase tree from a run's manifest.

    Used whenever a manifest exists — a completed run, a paused (``killed``)
    run, or a running run whose snapshot is being shown (see `RunRef.terminal`).
    A live ``running`` manifest gets no ``session.end`` span (the run hasn't
    ended); every other status closes the run with one. ``snapshot_stale_at``
    (an ISO time) is stamped on the root when the run has progressed past this
    snapshot (a resume) so the UI can flag the tree as a stale snapshot."""
    run_id = manifest["runId"]
    root_id = f"wfrun-{run_id}"
    start, end = _run_bounds(manifest)
    status = manifest.get("status") or "completed"
    agents, agent_ids = _manifest_agents(manifest)
    title = manifest.get("summary") or manifest.get("workflowName") or run_id

    spans = _root_and_title_spans(
        run_id, title=title, start=start, end=end,
        attrs={"agent_type": "claude", "model": manifest.get("defaultModel"),
               "cwd": _first_agent_value(agents_dir, agent_ids, "cwd"),
               "run_id": run_id, "task_id": manifest.get("taskId"),
               "parent_trace_id": _parent_trace_id(agents_dir),
               "workflow_name": manifest.get("workflowName"),
               "workflow_status": status, "agent_count": manifest.get("agentCount"),
               "snapshot_stale_at": snapshot_stale_at,
               "total_tokens": manifest.get("totalTokens"),
               "total_tool_calls": manifest.get("totalToolCalls")},
        is_test=is_test)

    phases, phase_ids = _phase_spans(run_id, root_id, manifest.get("phases") or [],
                                     start, end, is_test)
    spans.extend(phases)
    # Full per-agent results live in the journal (the manifest only previews).
    _, results = _journal_agents(_read_jsonl(agents_dir / "journal.jsonl"))
    for agent in agents:
        spans.extend(_full_agent_spans(
            run_id, root_id, phase_ids, agent, agents_dir, results, deep, is_test,
            run_start=start))

    # A live (still-running) manifest snapshot hasn't ended — don't close it
    # with a session.end (that would render an in-flight run as finished).
    if status != "running":
        spans.append(_span(run_id, f"wfend-{run_id}", "session.end", parent_id=root_id,
                           start_time=end, end_time=end,
                           status_code="OK" if status == "completed" else "ERROR",
                           attrs={"reason": status}, is_test=is_test))
    return spans


def _agent_full_prompt(agents_dir: Path, agent_id: str) -> str | None:
    """The agent's full dispatched prompt = its transcript's first user
    message (the manifest only carries a ~400-char preview). Iterates lazily —
    the prompt is the transcript's first entry, so the live poll path reads
    one line, not the whole streaming file."""
    for entry in _iter_jsonl(agents_dir / f"agent-{agent_id}.jsonl"):
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


def _agent_tool_count(turn_spans: list[dict], deep: bool, agent_id, fallback):
    """The agent header's tool-call count. When the deep tree was built, count
    the ``tool.*`` spans actually captured (and rendered) — this includes
    server-side tools like advisor and excludes the skipped StructuredOutput,
    so the header matches the rendered rows. The manifest's ``toolCalls``
    undercounts server-side tools, so it's only the fallback when no deep spans
    were built."""
    if not (deep and agent_id):
        return fallback
    return sum(1 for s in turn_spans if s["name"].startswith("tool."))


def _agent_bounds(agent: dict, run_start: str | None) -> tuple:
    """``(start_ts, end_ts)`` ISO strings for an agent span. Un-started (queued)
    agents have no ``startedAt`` — fall back to ``queuedAt`` then the run start
    so ``start_time`` is never null (the DB requires it)."""
    started = agent.get("startedAt")
    start_ts = _iso(started) or _iso(agent.get("queuedAt")) or run_start
    if started:
        return start_ts, _iso(started + (agent.get("durationMs") or 0))
    return start_ts, start_ts


def _full_agent_spans(run_id: str, root_id: str, phase_ids: dict[int, str],
                      agent: dict, agents_dir: Path, results: dict, deep: bool,
                      is_test: bool, run_start: str | None = None) -> list[dict]:
    """One agent's ``subagent.start`` span plus its deep turn children.

    Prompt + result are sourced from the transcript / journal (full text),
    not the manifest's truncated previews, so "Show full prompt" / the RESULT
    card show the complete content.
    """
    agent_id = agent.get("agentId")
    parent = phase_ids.get(agent.get("phaseIndex"), root_id)
    dur = agent.get("durationMs") or 0
    # A live/paused manifest also lists *queued* agents that have no agentId
    # yet; key those by their manifest index so they don't all collide on
    # ``wfagent-…-None`` (and render as one broken row).
    span_id = f"wfagent-{run_id}-{agent_id or 'q' + str(agent.get('index'))}"
    start_ts, end_ts = _agent_bounds(agent, run_start)
    full_prompt = (_agent_full_prompt(agents_dir, agent_id) if agent_id else None) \
        or agent.get("promptPreview")
    result_full = _result_text(results.get(agent_id)) or agent.get("resultPreview")
    turn_spans = _agent_turn_spans(run_id, span_id, agent_id, agents_dir, is_test) \
        if (deep and agent_id) else []
    tool_calls = _agent_tool_count(turn_spans, deep, agent_id, agent.get("toolCalls"))
    spans = [_span(
        run_id, span_id, "subagent.start", parent_id=parent,
        start_time=start_ts, end_time=end_ts, duration_ms=dur,
        attrs={"agent_id": agent_id, "agent_type": agent.get("agentType"),
               "agent_name": agent.get("label"), "label": agent.get("label"),
               "model": agent.get("model"), "state": agent.get("state"),
               "phase_title": agent.get("phaseTitle"),
               "prompt": full_prompt,
               "result_full": result_full,
               "result_preview": _preview(result_full),
               "tokens": agent.get("tokens"), "tool_calls": tool_calls},
        is_test=is_test)]
    spans.extend(turn_spans)
    return spans


def _journal_key_order(events: list[dict]) -> tuple[list[str], dict[str, str]]:
    """(distinct dispatch ``key``s in first-seen order, {key: latest agentId}).

    Each ``started`` event carries a ``key`` (a stable hash of the agent's
    dispatch input) and an ``agentId``. A paused→resumed run re-dispatches the
    same logical agent under the same key with a new agentId, so keying on
    ``key`` (keeping the latest agentId) collapses kill/resume duplicates to the
    canonical agent set — the same set the completion manifest records — in the
    order agents were first dispatched (which matches the script's source order;
    see `_parse_script_agents`)."""
    key_order: list[str] = []
    key_to_agent: dict[str, str] = {}
    for e in events:
        if e.get("type") != "started":
            continue
        key, agent_id = e.get("key"), e.get("agentId")
        if not key or not agent_id:
            continue
        if key not in key_to_agent:
            key_order.append(key)
        key_to_agent[key] = agent_id
    return key_order, key_to_agent


def _flat_agent_span(run_ref: RunRef, parent_id: str, agent_id: str,
                     results: dict, start: str | None, is_test: bool, *,
                     label: str | None = None,
                     phase_title: str | None = None) -> dict:
    """One agent's live ``subagent.start`` span with its current token total.

    ``parent_id`` is the run root in the coarse fallback, or the agent's
    ``workflow.phase`` span when the script let us map agents to phases live.
    ``label`` / ``phase_title`` (script-derived) are stamped so the rail shows
    real labels under real phases instead of generic ``workflow-subagent`` rows.
    """
    from lib.trace.transcript_usage import read_usage

    path = run_ref.agents_dir / f"agent-{agent_id}.jsonl"
    usage = read_usage(str(path), max_text_bytes=0) if path.exists() else None
    meta = _read_json(run_ref.agents_dir / f"agent-{agent_id}.meta.json") or {}
    # Store the FULL result (not just a preview) so the RESULT card can offer
    # "Show full" — mirrors `_full_agent_spans`. The journal carries the whole
    # result for a done agent; previewing it without keeping the full text left
    # the live path's RESULT card permanently trimmed at the preview cap.
    result_full = _result_text(results.get(agent_id))
    return _span(
        run_ref.run_id, f"wfagent-{run_ref.run_id}-{agent_id}", "subagent.start",
        parent_id=parent_id, start_time=start,
        attrs={"agent_id": agent_id, "agent_type": meta.get("agentType"),
               "label": label, "agent_name": label, "phase_title": phase_title,
               "state": "done" if agent_id in results else "running",
               # Dispatched prompt, available from the transcript the moment
               # the agent starts — mirrors `_full_agent_spans` so the task
               # prompt shows while the agent is still running.
               "prompt": _agent_full_prompt(run_ref.agents_dir, agent_id),
               "result_full": result_full,
               "result_preview": _preview(result_full),
               "tokens": usage.output_tokens if usage else None},
        is_test=is_test)


def _live_phase_list(decls: list[dict], script_path: Path | None) -> list | None:
    """The ordered ``[{title, detail}]`` phases for a confident live layout: the
    script's declared ``meta.phases`` (with detail), else derived from the
    agents' own phase values in first-seen order. None if any agent's phase is
    missing or names a phase outside the set — i.e. the parse model is wrong."""
    phases = _parse_script_phases(script_path)
    titles = [p["title"] for p in phases]
    if not titles:
        for d in decls:
            if d["phase"] and d["phase"] not in titles:
                titles.append(d["phase"])
        phases = [{"title": t, "detail": None} for t in titles]
    if any(d["phase"] not in titles for d in decls):
        return None
    return phases


def _confident_live_layout(run_ref: RunRef, root_id: str, key_order: list[str],
                           key_to_agent: dict[str, str], start: str | None,
                           is_test: bool) -> tuple | None:
    """``(phase_spans, [(agent_id, label, phase_title, parent_id)])`` when the
    script maps cleanly to the live agents — agents parented under synthesized
    ``workflow.phase`` spans, mirroring the completed tree. None (→ coarse flat
    fallback) when the script is dynamic, unmatched, or count-mismatched."""
    decls = _parse_script_agents(run_ref.script_path)
    if not decls or len(decls) != len(key_order):
        return None
    phases = _live_phase_list(decls, run_ref.script_path)
    if phases is None:
        return None
    phase_spans, phase_ids = _phase_spans(run_ref.run_id, root_id, phases,
                                          start, None, is_test)
    index_by_title = {p["title"]: i + 1 for i, p in enumerate(phases)}
    ordered = [(key_to_agent[k], d["label"], d["phase"],
                phase_ids[index_by_title[d["phase"]]])
               for k, d in zip(key_order, decls)]
    return phase_spans, ordered


def build_flat_spans(run_ref: RunRef, *, deep: bool = True,
                     is_test: bool = False) -> list[dict]:
    """Build the live tree for an *in-progress* run from the journal.

    No completion manifest exists yet. When the run's script can be statically
    mapped (`_confident_live_layout`), we synthesize the same
    ``workflow.phase`` → ``subagent.start`` structure the completed tree builds
    — real phases, real labels, agents marked running/done — so the run's own
    view renders identically to a finished run while still live. Otherwise we
    fall back to the coarse tree: agents hang directly off the run root and the
    rail shows the read-only declared phase plan above a flat "Running" band.

    When ``deep`` (the default), each agent's transcript is expanded into
    per-turn / per-tool spans via `_agent_turn_spans` (matching the full tree's
    ``wfagent-…`` ids, so the live→complete transition is idempotent).
    """
    run_id = run_ref.run_id
    root_id = f"wfrun-{run_id}"
    events = _read_jsonl(run_ref.journal_path)
    started, results = _journal_agents(events)
    key_order, key_to_agent = _journal_key_order(events)
    name, desc = _parse_script_meta(run_ref.script_path)
    start = _first_agent_value(run_ref.agents_dir, started, "timestamp") \
        or _iso(int(run_ref.state_mtime() * 1000))

    layout = _confident_live_layout(run_ref, root_id, key_order, key_to_agent,
                                    start, is_test)
    if layout is not None:
        phase_spans, agent_plan = layout
    else:
        # Fallback: today's coarse tree — raw started order, no phase mapping.
        phase_spans = []
        agent_plan = [(a, None, None, root_id) for a in started]

    spans = _root_and_title_spans(
        run_id, title=(desc or name or run_id), start=start, end=None,
        attrs={"agent_type": "claude", "workflow_name": name, "run_id": run_id,
               "workflow_status": "running",
               "parent_trace_id": run_ref.session_dir.name,
               "cwd": _first_agent_value(run_ref.agents_dir, started, "cwd"),
               # Declared phase plan from the script. The rail shows it as a
               # read-only plan only in the fallback (when no real phase spans
               # exist); once `_confident_live_layout` synthesizes phases it is
               # superseded but harmlessly retained.
               "phase_plan": _parse_script_phases(run_ref.script_path),
               "agent_count": len(agent_plan)},
        is_test=is_test)
    spans.extend(phase_spans)
    for agent_id, label, phase_title, parent_id in agent_plan:
        spans.append(_flat_agent_span(run_ref, parent_id, agent_id, results,
                                      start, is_test, label=label,
                                      phase_title=phase_title))
        if deep and agent_id:
            spans.extend(_agent_turn_spans(
                run_id, f"wfagent-{run_id}-{agent_id}", agent_id,
                run_ref.agents_dir, is_test))
    return spans
