"""Physically reap superseded PENDING placeholder spans from session_spans.

`session_spans` is append-only: the live `promptlive-`/`pending-`/`permreq-`
placeholder rows (`lib/trace/pending_spans.py`) are never deleted by ingest.
`lib/trace/merge.py::merge_spans` only HIDES them at read time once their
resolved counterpart lands, so they accumulate in the DB forever (~9% of the
store in practice). This module is the prune path the append-only store never
got: it deletes ONLY the placeholder rows `merge_spans` already hides AND whose
removal leaves the merged view unchanged — so the rendered trace is identical
before and after, while live in-flight placeholders and slash-command
expansion sources are preserved.

Safety rests on treating `merge_spans` as a black box. A placeholder is
reapable iff (a) it is absent from `merge_spans(raw)` — already superseded —
and (b) removing it from the raw window does not change the merged view's
signature. Clause (b) is what spares a slash-command `promptlive-` placeholder
whose full expansion merge copies onto its resolved anchor at read time
(`_absorb_slash_command_expansions`): physically deleting it WOULD change the
anchor's rendered text, so it is kept.
"""

from __future__ import annotations

import time

from lib.activity_log import get_activity_logger
from lib.orm.engine import get_connection
from lib.trace.merge import merge_spans
from lib.trace.pending_spans import is_pending_span_id
from lib.trace.projection import _fetch_spans

log = get_activity_logger("trace_ingest")

# Chunk DELETEs so a trace with many reapable rows never overruns SQLite's
# bound-variable ceiling.
_DELETE_CHUNK = 500


def _key(span: dict) -> tuple:
    return (span.get('trace_id'), span.get('span_id'))


def _view_signature(spans: list[dict]) -> dict:
    """What the rendered view depends on, per span: name, status, prompt text,
    and **rendered parentage** (`parent_id` + `turn_uuid`).

    Two windows with the same signature render the same conversation/timeline,
    so an equal signature before vs after a deletion proves the deletion is a
    no-op on what the user sees. This is ALWAYS computed over `merge_spans`
    output, whose `parent_id`/`turn_uuid` are already post-`_graft_orphans` —
    so the comparison is robust to a prior materialize (both sides re-graft
    deterministically), yet still catches `merge.py::_inherit_turn_linkage`:
    a slow tool's `pending-<tu>` placeholder can absorb the turn linkage
    (`turn_uuid` + `resp-` parent) that its resolved survivor never got, which
    merge transfers onto the survivor at read time. Omitting these fields would
    let the reaper delete that placeholder — the only copy — silently flipping
    the survivor's parent from its assistant-response branch to the prompt
    root. Capturing them keeps such a placeholder (the per-candidate fallback
    sees the survivor's parent change and spares it)."""
    sig: dict = {}
    for s in spans:
        attrs = s.get('attributes')
        text = attrs.get('text') if isinstance(attrs, dict) else None
        sig[_key(s)] = (s.get('name'), s.get('status_code'), text,
                        s.get('parent_id'), s.get('turn_uuid'))
    return sig


def _hidden_pending(raw: list[dict], merged: list[dict]) -> list[dict]:
    """Pending placeholder rows present in `raw` but dropped by `merge_spans`."""
    kept = {_key(s) for s in merged}
    return [
        s for s in raw
        if is_pending_span_id(s.get('span_id')) and _key(s) not in kept
    ]


def reapable_span_ids(raw: list[dict]) -> list[str]:
    """span_ids of placeholder rows physically safe to delete for one trace.

    Safe = merge already hides it AND removing it leaves the merged view's
    signature unchanged. Fast path: if removing ALL hidden placeholders at
    once preserves the signature, every one is independent. Slow path (only
    when a slash-expansion source is among them) tests each individually and
    keeps the ones the view still needs."""
    if not raw:
        return []
    merged = merge_spans(raw)
    base = _view_signature(merged)
    candidates = _hidden_pending(raw, merged)
    if not candidates:
        return []
    drop = {_key(c) for c in candidates}
    reduced = [s for s in raw if _key(s) not in drop]
    if _view_signature(merge_spans(reduced)) == base:
        return [c.get('span_id') for c in candidates]
    return _independently_safe(raw, candidates, base)


def _independently_safe(raw: list[dict], candidates: list[dict],
                        base: dict) -> list[str]:
    """Per-candidate fallback: keep any placeholder whose removal changes the
    merged view (its content is still rendered — e.g. an absorbed slash
    expansion); delete the rest."""
    safe: list[str] = []
    for c in candidates:
        ckey = _key(c)
        reduced = [s for s in raw if _key(s) != ckey]
        if _view_signature(merge_spans(reduced)) == base:
            safe.append(c.get('span_id'))
    return safe


def _candidate_trace_ids(conn, session: str | None, idle_minutes: int | None,
                         limit: int) -> list[str]:
    """Traces that carry at least one PENDING placeholder, honoring the
    `--session`, `--idle-minutes` and `--limit` filters."""
    sql = ("SELECT DISTINCT trace_id FROM session_spans "
           "WHERE status_code = 'PENDING'")
    params: list = []
    if session:
        sql += " AND trace_id = ?"
        params.append(session)
    if idle_minutes:
        sql += (" AND trace_id IN (SELECT trace_id FROM sessions "
                "WHERE last_seen < datetime('now', ?))")
        params.append(f"-{int(idle_minutes)} minutes")
    sql += " ORDER BY trace_id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r['trace_id'] for r in conn.execute(sql, params).fetchall()]


def _delete_spans(conn, trace_id: str, span_ids: list[str]) -> None:
    """Delete the placeholder rows from both the append-only span store and
    the structural map, chunked under SQLite's variable ceiling."""
    for i in range(0, len(span_ids), _DELETE_CHUNK):
        chunk = span_ids[i:i + _DELETE_CHUNK]
        ph = ",".join("?" * len(chunk))
        conn.execute(
            f"DELETE FROM session_spans WHERE trace_id = ? "
            f"AND span_id IN ({ph})", (trace_id, *chunk))
        conn.execute(
            f"DELETE FROM session_trace_map WHERE trace_id = ? "
            f"AND span_id IN ({ph})", (trace_id, *chunk))


def reap_pending_spans(*, session: str | None = None,
                       idle_minutes: int | None = None,
                       dry_run: bool = False, limit: int = 0) -> dict:
    """Reap superseded placeholder rows across candidate traces.

    Returns a summary dict. A `dry_run` writes and commits nothing. Safe
    against the live session: an in-flight placeholder whose resolved span
    hasn't arrived is kept by `merge_spans`, so it never becomes a candidate
    (`--idle-minutes` is an extra guard, not the only one)."""
    conn = get_connection()
    try:
        trace_ids = _candidate_trace_ids(conn, session, idle_minutes, limit)
        reaped = 0
        touched = 0
        for tid in trace_ids:
            ids = reapable_span_ids(_fetch_spans(conn, tid))
            if not ids:
                continue
            touched += 1
            reaped += len(ids)
            if not dry_run:
                _delete_spans(conn, tid, ids)
        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    record = log.read if dry_run else log.write
    record("pending_reaped", traces_scanned=len(trace_ids),
           traces_touched=touched, rows_reaped=reaped, dry_run=dry_run)
    return {
        "traces_scanned": len(trace_ids),
        "traces_touched": touched,
        "rows_reaped": reaped,
        "dry_run": dry_run,
    }


def reap_loop(*, interval_hours: float = 24.0, idle_minutes: int = 60) -> None:
    """Daemon loop: reap on a fixed interval until the process exits.

    Wraps each sweep so one bad pass can't kill the thread (the dashboard
    keeps serving regardless)."""
    interval_s = max(1.0, interval_hours * 3600.0)
    while True:
        try:
            reap_pending_spans(idle_minutes=idle_minutes)
        except Exception:  # daemon must survive a transient DB/merge error
            log.error("reap_loop_failed", exc_info=True)
        time.sleep(interval_s)
