"""Serve-time labels for workflow-subagent markers in a launching session.

Claude Code's SubagentStart hook payload carries no label for workflow
subagents, so a launching session's `subagent.start`/`subagent.stop` markers
render as indistinguishable "workflow-subagent" rows. The captured run trace
(`workflow_run_id` stamped on the `tool.Workflow` span) stores the same
agents WITH their script labels under identical agent_ids — join the label
in at serve time; the append-only store is never mutated.
"""

from __future__ import annotations

import time

_MARKER_NAMES = ("subagent.start", "subagent.stop")

# The live pages re-poll their session every few seconds and the markers stay
# label-less in the store forever, so the same cross-trace join would re-run
# on every poll (and again for the roster in the same request). A short TTL
# absorbs that; labels appearing after run-trace ingest lag by at most the TTL.
_CACHE_TTL_SEC = 30.0
_CACHE_MAX = 128
_cache: dict[tuple, tuple[float, dict]] = {}


def _is_unlabeled_workflow_marker(span: dict) -> bool:
    attrs = span.get("attributes") or {}
    return (span.get("name") in _MARKER_NAMES
            and attrs.get("agent_type") == "workflow-subagent"
            and not attrs.get("label"))


def _query_labels(trace_id: str, conn) -> dict:
    run_rows = conn.execute(
        "SELECT DISTINCT json_extract(attributes,'$.workflow_run_id') AS rid"
        "  FROM session_spans"
        " WHERE trace_id = ? AND name = 'tool.Workflow'",
        (trace_id,)).fetchall()
    run_ids = [r["rid"] for r in run_rows if r["rid"]]
    if not run_ids:
        return {}
    ph = ",".join("?" for _ in run_ids)
    rows = conn.execute(
        "SELECT json_extract(attributes,'$.agent_id') AS aid,"
        "       json_extract(attributes,'$.label') AS label"
        "  FROM session_spans"
        f" WHERE trace_id IN ({ph}) AND name = 'subagent.start'",
        tuple(run_ids)).fetchall()
    return {r["aid"]: r["label"] for r in rows if r["aid"] and r["label"]}


def workflow_agent_labels(trace_id: str, conn=None) -> dict:
    """agent_id → label from the run traces this session's workflows spawned.

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
        labels = _query_labels(trace_id, conn)
    else:
        own = _engine.get_connection()
        try:
            labels = _query_labels(trace_id, own)
        finally:
            own.close()
    _cache[key] = (now, labels)
    while len(_cache) > _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    return labels


def attach_workflow_agent_labels(trace_id: str, spans: list,
                                 conn=None) -> None:
    """Fill missing `label` on workflow-subagent markers, in place.

    Short-circuits without touching the DB when no marker needs a label."""
    needy = [s for s in spans if _is_unlabeled_workflow_marker(s)]
    if not needy:
        return
    labels = workflow_agent_labels(trace_id, conn)
    if not labels:
        return
    for s in needy:
        attrs = s.get("attributes") or {}
        label = labels.get(attrs.get("agent_id"))
        if label:
            attrs["label"] = label
            s["attributes"] = attrs
