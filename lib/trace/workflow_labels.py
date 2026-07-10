"""Serve-time enrichment of workflow-subagent markers in a launching session.

Claude Code's SubagentStart/Stop hooks carry only agent_id + agent_type for
workflow subagents, so a launching session's markers render as
indistinguishable "workflow-subagent" rows with no task prompt, result, or
model. The captured run trace (`workflow_run_id` stamped on the
`tool.Workflow` span) stores the same agents under identical agent_ids WITH
those attributes — join them in at serve time; the append-only store is
never mutated.
"""

from __future__ import annotations

import json
import time

# Attribute subset copied onto each marker kind. Fill-only — an attribute the
# hook DID capture is never overwritten. `result_full` stays behind in the run
# trace: it can be tens of KB and the launching session's card previews only.
_START_COPY_KEYS = ("label", "prompt", "model", "state", "tokens",
                    "tool_calls", "result_preview", "result_full")
_STOP_COPY_KEYS = ("label", "result_preview")
_MARKER_COPY_KEYS = {"subagent.start": _START_COPY_KEYS,
                     "subagent.stop": _STOP_COPY_KEYS}

# The live pages re-poll their session every few seconds and the markers stay
# bare in the store forever, so the same cross-trace join would re-run on
# every poll (and again for the roster in the same request). A short TTL
# absorbs that; enrichment appearing after run-trace ingest lags by at most
# the TTL.
_CACHE_TTL_SEC = 30.0
_CACHE_MAX = 128
_cache: dict[tuple, tuple[float, dict]] = {}


def _is_bare_workflow_marker(span: dict) -> bool:
    attrs = span.get("attributes") or {}
    return (span.get("name") in _MARKER_COPY_KEYS
            and attrs.get("agent_type") == "workflow-subagent"
            and not attrs.get("label"))


def _spawned_run_ids(trace_id: str, conn) -> list:
    rows = conn.execute(
        "SELECT DISTINCT json_extract(attributes,'$.workflow_run_id') AS rid"
        "  FROM session_spans"
        " WHERE trace_id = ? AND name = 'tool.Workflow'",
        (trace_id,)).fetchall()
    return [r["rid"] for r in rows if r["rid"]]


def _parse_attrs(raw) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def _query_agent_attrs(trace_id: str, conn) -> dict:
    run_ids = _spawned_run_ids(trace_id, conn)
    if not run_ids:
        return {}
    ph = ",".join("?" for _ in run_ids)
    rows = conn.execute(
        "SELECT attributes FROM session_spans"
        f" WHERE trace_id IN ({ph}) AND name = 'subagent.start'",
        tuple(run_ids)).fetchall()
    out = {}
    for r in rows:
        attrs = _parse_attrs(r["attributes"])
        aid = attrs.get("agent_id")
        if aid and attrs.get("label"):
            out[aid] = {k: attrs[k] for k in _START_COPY_KEYS if attrs.get(k)}
    return out


def workflow_agent_attrs(trace_id: str, conn=None) -> dict:
    """agent_id → attribute subset from the run traces this session's
    workflows spawned.

    Empty when the session launched no workflow or no run trace is ingested
    yet — callers then leave their rows untouched (graceful fallback). Pass
    the connection you already hold; otherwise one is opened for the call.
    Results are cached briefly (keyed per DB so tests never cross-pollute)."""
    from lib.orm import engine as _engine

    key = (str(_engine.DB_PATH), trace_id)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < _CACHE_TTL_SEC:
        return hit[1]
    if conn is not None:
        agent_attrs = _query_agent_attrs(trace_id, conn)
    else:
        own = _engine.get_connection()
        try:
            agent_attrs = _query_agent_attrs(trace_id, own)
        finally:
            own.close()
    _cache[key] = (now, agent_attrs)
    while len(_cache) > _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    return agent_attrs


def attach_workflow_agent_attrs(trace_id: str, spans: list,
                                conn=None) -> None:
    """Enrich bare workflow-subagent markers in place (fill-only).

    Short-circuits without touching the DB when no marker needs it."""
    needy = [s for s in spans if _is_bare_workflow_marker(s)]
    if not needy:
        return
    agent_attrs = workflow_agent_attrs(trace_id, conn)
    if not agent_attrs:
        return
    for s in needy:
        _fill_marker(s, agent_attrs)


def _fill_marker(span: dict, agent_attrs: dict) -> None:
    attrs = span.get("attributes") or {}
    src = agent_attrs.get(attrs.get("agent_id"))
    if not src:
        return
    for k in _MARKER_COPY_KEYS[span["name"]]:
        if src.get(k) and not attrs.get(k):
            attrs[k] = src[k]
    span["attributes"] = attrs
