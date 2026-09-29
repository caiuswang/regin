"""Append-only span-row writes shared by the span and usage ingest paths."""

from __future__ import annotations

import json

from lib.activity_log import get_activity_logger as _get_activity_logger


def _trace_log():
    return _get_activity_logger("trace_ingest")


_TRANSCRIPT_ID_PREFIXES = ('prompt-', 'resp-', 'think-', 'cmd-')


def _infer_source(span_id) -> str:
    """Best-effort capture-source tag for the append-only row: transcript-scan
    spans carry deterministic id prefixes, everything else is a live hook
    event. Audit/debug metadata only — the serve-time merge (lib/trace/merge.py)
    keys on span_id/name, never on this."""
    sid = span_id or ''
    return 'transcript' if sid.startswith(_TRANSCRIPT_ID_PREFIXES) else 'hook'


def _invalidate_projection(trace_id) -> None:
    """Drop the read-side memo for a trace this process just wrote.

    Lives at the lowest write level because the upsert below rewrites a span
    IN PLACE, preserving its row id — so no read-side aggregate can notice an
    assistant response growing, and only the writer can say the answer moved.
    """
    if not trace_id:
        return
    from lib.trace.trace_service import projection_cache
    projection_cache.invalidate(trace_id)


def _insert_span_row(conn, span, attrs) -> None:
    """Append one normalised span into both span tables, keyed by
    (trace_id, span_id). The store is APPEND-ONLY: a placeholder and its real
    anchor coexist as distinct rows and the serve-time merge selects the
    winner — no in-place promotion, no deletion. Idempotent: a re-ingest
    UPDATEs the structural fields in place, preserving the DB row id and any
    token-attribution columns (input/image/cost) that ingest_tool_attribution
    backfilled later."""
    # Promote attributes.output_tokens to its own column on assistant_response
    # / assistant.thinking spans so fetch_tool_token_rollup can aggregate them
    # alongside tool.* spans (tool spans get this filled by the attribution
    # UPDATE; assistant.* carry it inline because there's no separate post).
    out_tok = None
    if span.get('name') in ('assistant_response', 'assistant.thinking'):
        raw = attrs.get('output_tokens')
        if isinstance(raw, (int, float)):
            out_tok = int(raw)
    agent_id = attrs.get('agent_id')
    source_prompt_id = attrs.get('source_prompt_id')
    conn.execute(
        """INSERT INTO session_spans
           (trace_id, span_id, parent_id, name, kind, start_time,
            end_time, duration_ms, attributes, status_code, status_message,
            output_tokens, agent_id, source_prompt_id, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(trace_id, span_id) DO UPDATE SET
             parent_id = excluded.parent_id,
             name = excluded.name,
             kind = excluded.kind,
             start_time = excluded.start_time,
             end_time = excluded.end_time,
             duration_ms = excluded.duration_ms,
             attributes = excluded.attributes,
             status_code = excluded.status_code,
             status_message = excluded.status_message,
             output_tokens = COALESCE(excluded.output_tokens, session_spans.output_tokens),
             agent_id = COALESCE(excluded.agent_id, session_spans.agent_id),
             source_prompt_id = COALESCE(excluded.source_prompt_id, session_spans.source_prompt_id),
             source = excluded.source""",
        (span.get('trace_id'), span.get('span_id'),
         span.get('parent_id'), span.get('name'),
         span.get('kind', 'internal'), span.get('start_time'),
         span.get('end_time'), span.get('duration_ms'),
         json.dumps(attrs),
         span.get('status_code', 'UNSET'), span.get('status_message'),
         out_tok,
         agent_id if isinstance(agent_id, str) and agent_id else None,
         source_prompt_id if isinstance(source_prompt_id, str) and source_prompt_id else None,
         _infer_source(span.get('span_id'))),
    )
    # Dual-write structural data to session_trace_map so the frontend can load
    # the full session shape without the potentially-large attributes blobs.
    conn.execute(
        """INSERT INTO session_trace_map
           (trace_id, span_id, parent_id, name, kind, start_time,
            end_time, duration_ms, status_code, status_message)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(trace_id, span_id) DO UPDATE SET
             parent_id = excluded.parent_id,
             name = excluded.name,
             kind = excluded.kind,
             start_time = excluded.start_time,
             end_time = excluded.end_time,
             duration_ms = excluded.duration_ms,
             status_code = excluded.status_code,
             status_message = excluded.status_message""",
        (span.get('trace_id'), span.get('span_id'),
         span.get('parent_id'), span.get('name'),
         span.get('kind', 'internal'), span.get('start_time'),
         span.get('end_time'), span.get('duration_ms'),
         span.get('status_code', 'UNSET'), span.get('status_message')),
    )
    # Eager, and before the commit: invalidating a write that later rolls back
    # costs one recompute, while missing one serves a stale tree.
    _invalidate_projection(span.get('trace_id'))


def _detect_existing_spans(conn, normalised: list[tuple]) -> tuple[set, int]:
    """Return (existing_set, skipped) for a batch.

    `existing_set` holds the (trace_id, span_id) pairs already present in
    session_spans; `skipped` counts how many entries in `normalised` are
    re-ingests. Empty batch -> (set(), 0).
    """
    if not normalised:
        return set(), 0
    pairs = {(s.get('trace_id'), s.get('span_id'))
             for s, _ in normalised}
    placeholders = ','.join(['(?, ?)'] * len(pairs))
    flat = [v for p in pairs for v in p]
    existing = conn.execute(
        f"SELECT trace_id, span_id FROM session_spans "
        f"WHERE (trace_id, span_id) IN (VALUES {placeholders})",
        flat,
    ).fetchall()
    existing_set = {(r['trace_id'], r['span_id']) for r in existing}
    skipped = sum(
        1 for s, _ in normalised
        if (s.get('trace_id'), s.get('span_id')) in existing_set
    )
    return existing_set, skipped
