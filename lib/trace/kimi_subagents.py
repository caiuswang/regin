"""Reconcile Kimi Code subagent activity into nested subagent traces.

Claude Code runs a subagent in isolation: its tool calls never fire the
parent's hooks, so the only parent-session spans are `subagent.start` /
`subagent.stop` plus the subagent's replayed assistant turns. Kimi Code is
different — it fires `PreToolUse` / `PostToolUse` for a subagent's tool calls
under the PARENT `session_id`. Without help those land as flat session spans
in the main trace, mixed in with the main agent's own tools.

The hook payloads carry no subagent identifier, but Kimi writes each
subagent's own event stream to
``<sessions>/wd_*/<sid>/agents/agent-N/wire.jsonl`` (sibling of ``main``), and
a tool call's ``toolCallId`` there equals the parent hook's ``tool_use_id``.
This pass reads those sibling wires and:

  * stamps ``attributes.agent_id`` onto each subagent-owned tool span, so the
    serve-time ``_reparent_subagents`` (lib/trace/projection.py) nests it under
    the subagent;
  * enriches the hook-emitted ``subagent.start`` / ``subagent.stop`` markers
    with that ``agent_id``, the subagent's identity, and its final response
    (Kimi's ``SubagentStop`` carries the summary as ``response``); and
  * emits the subagent's own ``assistant_response`` / ``assistant.thinking``
    turns, also tagged ``agent_id`` (parity with Claude's
    ``emit_subagent_responses``).

``agent_id`` is the launching ``tool.Agent`` call's ``tool_use_id``, recovered
by matching that span's stored ``prompt`` as a substring of the subagent
wire's first prompt. Idempotent: deterministic span ids + value-stable UPDATEs,
safe to re-run on every ``SubagentStop`` and during backfill.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

from lib.activity_log import get_activity_logger

_log = get_activity_logger("trace_ingest")

_RESULT_PREVIEW_MAX = 200
# Match enough of the launch prompt to be unambiguous without tripping over
# the trailing-whitespace / cap differences between the stored span prompt and
# the git-context-wrapped copy in the subagent wire.
_PROMPT_MATCH_CHARS = 200


def discover_subagent_sessions() -> list[str]:
    """Trace ids of Kimi sessions that have at least one subagent wire dir
    (``agents/agent-*/wire.jsonl``). Used by the backfill CLI to find every
    session whose subagent spans still need nesting."""
    from lib.providers.kimi import KimiProvider
    base = KimiProvider().transcript_projects_dir()
    out: list[str] = []
    seen: set[str] = set()
    for p in glob.glob(str(base / "*" / "*" / "agents" / "agent-*" / "wire.jsonl")):
        sid = Path(p).parents[2].name  # agent-N -> agents -> <session_id>
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def _agents_dir(trace_id: str) -> Path | None:
    """The ``agents/`` directory for a Kimi session, or None if not found."""
    from lib.providers.kimi import KimiProvider
    base = KimiProvider().transcript_projects_dir()
    matches = glob.glob(str(base / "*" / trace_id / "agents"))
    return Path(matches[0]) if matches else None


def _agent_index(name: str) -> int:
    """Numeric suffix of an ``agent-N`` dir name (for creation-order sort)."""
    try:
        return int(name.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def _subagent_wires(agents_dir: Path) -> list[tuple[str, str]]:
    """`(dir_name, wire_path)` for each non-main subagent, in creation order."""
    out: list[tuple[str, str]] = []
    for d in agents_dir.glob("agent-*"):
        wire = d / "wire.jsonl"
        if wire.is_file():
            out.append((d.name, str(wire)))
    out.sort(key=lambda t: _agent_index(t[0]))
    return out


def _load_launches(conn, trace_id: str) -> list[dict]:
    """The session's `tool.Agent` launch spans: one entry per distinct
    `tool_use_id`, carrying its stored prompt / subagent_type / description."""
    rows = conn.execute(
        "SELECT tool_use_id, attributes FROM session_spans "
        "WHERE trace_id = ? AND name = 'tool.Agent'",
        (trace_id,),
    ).fetchall()
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        attrs = json.loads(r["attributes"])
        tu = r["tool_use_id"] or attrs.get("tool_use_id")
        if not tu or tu in seen:
            continue
        seen.add(tu)
        out.append({
            "tool_use_id": tu,
            "prompt": (attrs.get("prompt") or "").strip(),
            "subagent_type": attrs.get("subagent_type"),
            "description": attrs.get("description"),
        })
    return out


def _match_launch(first_prompt: str, launches: list[dict], used: set[str]) -> dict | None:
    """The launch whose prompt is a prefix-substring of the subagent's first
    prompt and not already claimed."""
    for lc in launches:
        head = lc["prompt"][:_PROMPT_MATCH_CHARS]
        if head and lc["tool_use_id"] not in used and head in first_prompt:
            return lc
    return None


def _first_prompt(usage) -> str:
    """The subagent's first prompt text (the git-context-wrapped launch)."""
    for text in usage.prompt_texts.values():
        return text
    return ""


def _tool_ids(usage) -> list[str]:
    """Every tool-call id the subagent issued in its own wire."""
    ids: list[str] = []
    for turn in usage.turns:
        for tc in turn.tool_calls:
            tid = tc.get("id")
            if tid:
                ids.append(tid)
    return ids


def _turn_bounds(usage) -> tuple[str | None, str | None]:
    """`(first_ts, last_ts)` over the subagent's timestamped turns — the
    anchor times for an inserted start / stop marker."""
    times = [t.timestamp for t in usage.turns if t.timestamp]
    return (times[0], times[-1]) if times else (None, None)


def _result_preview(usage) -> str | None:
    """A flattened preview of the subagent's last assistant text."""
    last = ""
    for turn in usage.turns:
        if turn.text:
            last = turn.text
    if not last:
        return None
    flat = " ".join(str(last).split())
    if len(flat) > _RESULT_PREVIEW_MAX:
        flat = flat[:_RESULT_PREVIEW_MAX] + "…"
    return flat


def _stamp_tool_spans(conn, trace_id: str, agent_id: str, tool_ids: list[str]) -> int:
    """Set `attributes.agent_id` on every subagent-owned tool span (matched by
    `tool_use_id` column or `attributes.tool_use_id`). Returns rows touched."""
    if not tool_ids:
        return 0
    ph = ",".join("?" * len(tool_ids))
    cur = conn.execute(
        f"UPDATE session_spans "
        f"   SET attributes = json_set(attributes, '$.agent_id', ?) "
        f" WHERE trace_id = ? "
        f"   AND (tool_use_id IN ({ph}) "
        f"        OR json_extract(attributes, '$.tool_use_id') IN ({ph}))",
        [agent_id, trace_id, *tool_ids, *tool_ids],
    )
    return cur.rowcount


def _enrich_marker(conn, row, agent_id: str, info: dict) -> None:
    """Add agent_id / identity / result_preview to an existing
    `subagent.start` or `subagent.stop` span, preserving other attributes."""
    attrs = json.loads(row["attributes"])
    attrs["agent_id"] = agent_id
    if info.get("agent_name"):
        attrs.setdefault("agent_name", info["agent_name"])
    if info.get("description"):
        attrs.setdefault("description", info["description"])
    if info.get("result_preview") and not attrs.get("result_preview"):
        attrs["result_preview"] = info["result_preview"]
    conn.execute(
        "UPDATE session_spans SET attributes = ? WHERE trace_id = ? AND span_id = ?",
        (json.dumps(attrs), row["trace_id"], row["span_id"]),
    )


def _insert_marker(conn, trace_id: str, name: str, agent_id: str,
                   ts: str, info: dict) -> None:
    """Emit a fresh `subagent.start` / `subagent.stop` for a session whose
    hooks never recorded one (older Kimi sessions). Deterministic span id keyed
    on agent_id keeps it idempotent; parent-less so the chronological graft
    nests it under the launching prompt."""
    from lib.trace.trace_service.ingest import _insert_span_row
    kind = "start" if name.endswith("start") else "stop"
    attrs: dict = {"agent_id": agent_id}
    if info.get("agent_name"):
        attrs["agent_name"] = info["agent_name"]
    if info.get("description"):
        attrs["description"] = info["description"]
    if kind == "stop" and info.get("result_preview"):
        attrs["result_preview"] = info["result_preview"]
    span = {
        "trace_id": trace_id,
        "span_id": f"sa-{kind}-{agent_id}",
        "parent_id": None,
        "name": name,
        "kind": "internal",
        "start_time": ts,
        "end_time": ts,
        "duration_ms": 0,
        "status_code": "OK",
        "status_message": None,
    }
    _insert_span_row(conn, span, attrs)


def _overlap(a: str, b: str, n: int = 80) -> bool:
    """True when the leading `n` chars of either string sit inside the other —
    a cheap content match tolerant of the truncation/wrapping differences
    between a stored marker preview and a wire-derived prompt/response."""
    return bool(a and b and (a[:n] in b or b[:n] in a))


def _match_marker(pool: list, key: str | None, attr: str):
    """Claim one hook marker from `pool` (removing it): the marker whose stored
    `attr` overlaps `key` (robust against out-of-order parallel subagents),
    else the first remaining (creation-order fallback for older markers that
    predate the stored preview). None when the pool is empty."""
    if key:
        for i, m in enumerate(pool):
            value = json.loads(m["attributes"]).get(attr) or ""
            if _overlap(value, key):
                return pool.pop(i)
    return pool.pop(0) if pool else None


def _place_markers(conn, trace_id: str, agent_id: str, info: dict, usage,
                   starts: list, stops: list, launch_prompt: str | None) -> None:
    """Claim + enrich this subagent's hook start/stop markers, or insert fresh
    ones when the session recorded none. `starts`/`stops` are mutated (claimed
    markers are popped) so each is bound to exactly one subagent."""
    first_ts, last_ts = _turn_bounds(usage)
    start = _match_marker(starts, launch_prompt, "prompt_preview")
    if start is not None:
        _enrich_marker(conn, start, agent_id, info)
    elif first_ts:
        _insert_marker(conn, trace_id, "subagent.start", agent_id, first_ts, info)
    stop = _match_marker(stops, info.get("result_preview"), "result_preview")
    if stop is not None:
        _enrich_marker(conn, stop, agent_id, info)
    elif last_ts:
        _insert_marker(conn, trace_id, "subagent.stop", agent_id, last_ts, info)


def _claim_markers(conn, trace_id: str, name: str) -> list:
    """Every subagent marker of one kind for the trace, oldest first — the
    claimable pool (mutated as subagents claim from it). Includes markers a
    prior run already enriched so re-running re-binds the SAME marker by
    content/order rather than inserting a duplicate (idempotency)."""
    return conn.execute(
        "SELECT trace_id, span_id, attributes FROM session_spans "
        " WHERE trace_id = ? AND name = ? "
        " ORDER BY start_time ASC, id ASC",
        (trace_id, name),
    ).fetchall()


def _emit_subagent_turns(conn, trace_id: str, agent_id: str, usage) -> int:
    """Insert the subagent's assistant_response / assistant.thinking turns,
    tagged agent_id. Deterministic span ids make this idempotent."""
    from hook_manager.handlers.subagent_lifecycle import (
        _normalize_subagent_ts, _subagent_turn_attributes, _subagent_turn_emittable,
    )
    from lib.trace.trace_service.ingest import _insert_span_row
    emitted = 0
    for idx, turn in enumerate(usage.turns):
        if not _subagent_turn_emittable(turn, None):
            continue
        has_text = bool(turn.text)
        ts = _normalize_subagent_ts(turn.timestamp)
        attrs = _subagent_turn_attributes(turn, idx, agent_id, usage.model)
        span = {
            "trace_id": trace_id,
            "span_id": f'{"resp-sa" if has_text else "think-sa"}-{turn.uuid[:13]}',
            "parent_id": None,
            "name": "assistant_response" if has_text else "assistant.thinking",
            "kind": "internal",
            "start_time": ts,
            "end_time": ts,
            "duration_ms": int(turn.inference_duration_ms or 0),
            "status_code": "OK",
            "status_message": None,
        }
        _insert_span_row(conn, span, attrs)
        emitted += 1
    return emitted


def _reconcile_one(conn, trace_id: str, wire_path: str, launches: list[dict],
                   used: set[str], starts: list, stops: list, idx: int) -> dict:
    """Reconcile a single subagent wire. Returns a small stats dict."""
    from lib.trace.kimi_transcript import read_usage_kimi
    usage = read_usage_kimi(wire_path)
    if usage is None:
        return {"agent_id": None, "tool_spans": 0, "turns": 0}
    launch = _match_launch(_first_prompt(usage), launches, used)
    if launch:
        used.add(launch["tool_use_id"])
        agent_id = launch["tool_use_id"]
        agent_name = launch.get("subagent_type")
        description = launch.get("description")
        launch_prompt = launch.get("prompt")
    else:
        agent_id = f"{trace_id}:agent-{idx}"
        agent_name = "subagent"
        description = None
        launch_prompt = None
    info = {
        "agent_name": agent_name,
        "description": description,
        "result_preview": _result_preview(usage),
    }
    touched = _stamp_tool_spans(conn, trace_id, agent_id, _tool_ids(usage))
    _place_markers(conn, trace_id, agent_id, info, usage, starts, stops, launch_prompt)
    turns = _emit_subagent_turns(conn, trace_id, agent_id, usage)
    return {"agent_id": agent_id, "tool_spans": touched, "turns": turns}


def reconcile_kimi_subagents(trace_id: str) -> dict:
    """Nest a Kimi session's flat subagent spans under their subagent trace.

    Reads the session's sibling ``agents/agent-*/wire.jsonl`` streams, stamps
    ``agent_id`` onto the subagent-owned tool spans, enriches the
    ``subagent.start`` / ``subagent.stop`` markers, and replays the subagents'
    assistant turns. Returns ``{subagents, tool_spans, turns}`` counts. A no-op
    (``subagents: 0``) when the session has no subagent dirs. Idempotent.
    """
    if not isinstance(trace_id, str) or not trace_id:
        return {"subagents": 0, "tool_spans": 0, "turns": 0}
    agents_dir = _agents_dir(trace_id)
    if agents_dir is None:
        return {"subagents": 0, "tool_spans": 0, "turns": 0}
    wires = _subagent_wires(agents_dir)
    if not wires:
        return {"subagents": 0, "tool_spans": 0, "turns": 0}

    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        launches = _load_launches(conn, trace_id)
        starts = _claim_markers(conn, trace_id, "subagent.start")
        stops = _claim_markers(conn, trace_id, "subagent.stop")
        used: set[str] = set()
        tool_spans = turns = 0
        for idx, (_name, wire_path) in enumerate(wires):
            stats = _reconcile_one(
                conn, trace_id, wire_path, launches, used, starts, stops, idx,
            )
            tool_spans += stats["tool_spans"]
            turns += stats["turns"]
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    result = {"subagents": len(wires), "tool_spans": tool_spans, "turns": turns}
    _log.write("kimi_subagents_reconciled", trace_id=trace_id, **result)
    return result
