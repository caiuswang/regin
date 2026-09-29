"""Per-turn usage rows for one session, each with the spans it owns."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional


def _to_utc(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime.

    The join between `turn_usage` and `session_spans` straddles two
    timestamp conventions: `turn_usage.timestamp` is UTC with a `Z`
    suffix (Claude Code's transcript writes it that way), whereas
    `session_spans.start_time` is naive-local ISO (every hook emitter
    calls `datetime.now().isoformat()`, which omits tz info). A naive
    string compare would silently mis-group turns and spans whenever
    the user isn't in UTC. This helper returns one tz-aware datetime
    from either shape so callers can compare safely; naive inputs are
    promoted to the host's local zone first, then converted to UTC.

    Returns None for empty / unparseable input so the caller can treat
    a missing timestamp as "no constraint" instead of crashing.
    """
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Naive → assume host local.
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc)


# Span names that are structural scaffolding (prompt boundaries,
# session/turn lifecycle markers, conversation root) rather than
# in-turn activity. Excluded from per-turn span_refs so the sidebar
# shows the user actual tool/skill/file activity, not the scaffolding.
_TURN_SPAN_EXCLUDE = frozenset({
    'prompt', 'task.notification', 'turn', 'conversation',
    'session.start', 'session.end',
})


def _fetch_turn_usage_rows(conn, trace_id: str) -> tuple[list[dict], list, set]:
    """Read the three raw inputs `fetch_turn_usage` needs from the DB.

    Returns `(turns, span_rows, server_side_uuids)`. When there are no
    turns, `span_rows`/`server_side_uuids` are empty (the span queries
    are skipped, matching the original early-return behavior).
    """
    rows = conn.execute("""
        SELECT turn_uuid, turn_index, timestamp, model,
               input_tokens, output_tokens,
               cache_read_tokens, cache_creation_tokens,
               context_used_tokens, request_id, effort_level, cost_usd
        FROM turn_usage
        WHERE trace_id = ?
        ORDER BY timestamp ASC, turn_index ASC
    """, (trace_id,)).fetchall()
    turns = [dict(r) for r in rows]
    if not turns:
        return turns, [], set()

    # Exclude live PENDING placeholders so a pending tool span and its
    # resolved twin don't both land in a turn's span_refs/tool_summary
    # (the append-only store keeps both until the read-time merge).
    span_rows = conn.execute("""
        SELECT span_id, name, start_time, attributes
        FROM session_spans
        WHERE trace_id = ?
          AND status_code != 'PENDING'
        ORDER BY start_time ASC
    """, (trace_id,)).fetchall()
    # Set of turn_uuids whose API call rolled in a server-side
    # sub-call (advisor today). The parent turn's `context_used`
    # bundles the sub-call's tokens, so the UI labels those rows
    # so the user knows that ctx_pct doesn't reflect main context.
    server_side_uuids = {
        r[0] for r in conn.execute(
            """SELECT DISTINCT
                      COALESCE(turn_uuid,
                               json_extract(attributes, '$.turn_uuid'))
                 FROM session_spans
                WHERE trace_id = ?
                  AND json_extract(attributes, '$.server_side') = 1
            """,
            (trace_id,),
        ).fetchall()
        if r[0] is not None
    }
    return turns, span_rows, server_side_uuids


def _owning_turn_index(dt: datetime, turn_bounds: list) -> Optional[int]:
    """Index of the first turn whose bound `>= dt` — the turn that owns
    a span starting at `dt`. `None` when `dt` is after the last turn's
    timestamp (an in-flight span the next turn_usage fire will adopt).
    """
    for i, bound in enumerate(turn_bounds):
        if bound is not None and dt <= bound:
            return i
    return None


def _assign_spans_to_turns(span_rows: list, turn_bounds: list) -> list[list[dict]]:
    """Bucket spans into per-turn lists. The i-th bucket receives spans
    whose start_time is in `(turn_bounds[i-1], turn_bounds[i]]`.
    Structural names and unparseable timestamps are skipped.
    """
    buckets: list[list[dict]] = [[] for _ in turn_bounds]
    for sr in span_rows:
        name = sr['name']
        if name in _TURN_SPAN_EXCLUDE:
            continue
        dt = _to_utc(sr['start_time'])
        if dt is None:
            continue
        idx = _owning_turn_index(dt, turn_bounds)
        if idx is None:
            continue
        try:
            attrs = json.loads(sr['attributes']) if sr['attributes'] else {}
        except (TypeError, ValueError):
            attrs = {}
        tool_name = attrs.get('tool_name') if isinstance(attrs.get('tool_name'), str) else None
        buckets[idx].append({
            'span_id': sr['span_id'],
            'name': name,
            'start_time': sr['start_time'],
            'tool_name': tool_name,
        })
    return buckets


def _tool_summary(bucket: list[dict]) -> list[dict]:
    """Deduped `[{name, count}]` for a turn's spans, ranked by count
    desc then name. Falls back to span `name` when `tool_name` is None.
    """
    counts: dict[str, int] = {}
    for s in bucket:
        key = s['tool_name'] or s['name']
        counts[key] = counts.get(key, 0) + 1
    return sorted(
        ({'name': k, 'count': v} for k, v in counts.items()),
        key=lambda e: (-e['count'], e['name']),
    )


def fetch_turn_usage(trace_id: str, *, with_refs: bool = True) -> list[dict]:
    """Return per-turn usage rows for a trace, oldest timestamp first.

    Each row carries the raw token counters from `turn_usage` plus
    three derived fields the trace UI uses to tie turns to spans:

    - `span_refs` — list of `{span_id, name, start_time, tool_name}`
      for every span whose `start_time` falls in the turn's interval
      `(prev_turn.ts, this_turn.ts]`. Structural names
      (`prompt`, `turn`, `session.*`, `conversation`) are filtered out
      so the UI sees in-turn activity only.
    - `span_count` — convenience len of `span_refs`.
    - `tool_summary` — deduped `[{name, count}]` list sorted by
      descending count, drawn from each span's `attributes.tool_name`
      (falls back to the span `name` for non-tool spans like
      `skill.read`, `file.edit`). Lets the row render a compact
      "Read×2·Bash" summary without client-side grouping.

    `with_refs=False` keeps `span_count` but drops the two heavy fields.
    Both are read only by the turns SIDEBAR, which lives behind the detail
    rail and is closed on a fresh conversation-tab load — while the feed's
    own per-turn footers and the TOC need nothing but the counters. On a
    223-turn session those two were 86KB of a 180KB first-open response.

    First-turn interval starts at `-inf` so any span preceding the
    first turn's timestamp gets attributed there rather than dropped.
    """
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        turns, span_rows, server_side_uuids = _fetch_turn_usage_rows(conn, trace_id)
    finally:
        conn.close()

    if not turns:
        return turns

    turn_bounds = [_to_utc(t.get('timestamp')) for t in turns]
    buckets = _assign_spans_to_turns(span_rows, turn_bounds)

    for turn, bucket in zip(turns, buckets):
        turn['span_count'] = len(bucket)
        if with_refs:
            turn['span_refs'] = bucket
            turn['tool_summary'] = _tool_summary(bucket)
        turn['is_server_side'] = turn.get('turn_uuid') in server_side_uuids

    return turns
