"""Read-side queries for the trace dashboard.

Pagination + projection helpers behind the /api/skill-reads,
/api/mcp-calls, /api/sessions/<trace_id>/spans, and per-turn endpoints.
SQL is raw because SQLite-specific `json_extract` + CTEs don't translate
cleanly to SQLAlchemy. `get_connection` is imported lazily so tests can
monkey-patch `lib.orm.engine.get_connection`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from lib.trace.is_test import _IS_TEST_CASE, _IS_TEST_WHERE
from lib.utils.pagination import CursorPage, keyset_page


# ── Skill reads ──────────────────────────────────────────────

def list_skill_reads_page(
    *,
    skill_filter: Optional[str],
    session_filter: Optional[str],
    include_tests: bool,
    cursor_token: Optional[str],
    size: int,
) -> tuple[CursorPage, list[dict], list[dict]]:
    """Paginated skill-reads feed + first-page stats/sessions.

    Returns (page, stats, sessions). `stats` and `sessions` are empty
    lists when `cursor_token` is non-None (mid-feed pagination skips
    the summary queries).
    """
    from lib.orm.engine import get_connection

    test_exclusion = "" if include_tests else """session_id NOT IN (
        SELECT DISTINCT trace_id FROM session_spans
        WHERE json_extract(attributes, '$.is_test') = 1
    )"""

    conditions: list[str] = []
    params: list = []
    if skill_filter:
        conditions.append("skill_id = ?")
        params.append(skill_filter)
    if session_filter:
        conditions.append("session_id = ?")
        params.append(session_filter)
    if test_exclusion:
        conditions.append(test_exclusion)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    base_sql = f"SELECT * FROM skill_reads{where}"

    conn = get_connection()
    try:
        page = keyset_page(
            conn, base_sql, params,
            order_cols=[("read_at", "DESC"), ("id", "DESC")],
            cursor_token=cursor_token, size=size,
        )

        stats: list[dict] = []
        sessions: list[dict] = []
        if cursor_token is None:
            stats_where = f" WHERE {test_exclusion}" if test_exclusion else ""
            stats_rows = conn.execute(f"""
                SELECT skill_id, COUNT(*) as total, MAX(read_at) as last_seen
                FROM skill_reads{stats_where}
                GROUP BY skill_id ORDER BY last_seen DESC
            """).fetchall()
            stats = [dict(r) for r in stats_rows]

            sessions_where = (
                'WHERE ' + test_exclusion.replace(
                    'session_id NOT IN', 'sr.session_id NOT IN'
                )
            ) if test_exclusion else ''
            sessions_rows = conn.execute(f"""
                WITH plan_latest AS (
                    SELECT session_id, plan_filename,
                           ROW_NUMBER() OVER (
                               PARTITION BY session_id ORDER BY started_at DESC
                           ) AS rn
                    FROM plan_sessions
                ),
                test_markers AS (
                    SELECT trace_id,
                           {_IS_TEST_CASE} AS is_test,
                           MAX(json_extract(attributes, '$.test_name')) AS test_name
                    FROM session_spans
                    WHERE {_IS_TEST_WHERE}
                       OR json_extract(attributes, '$.test_name') IS NOT NULL
                    GROUP BY trace_id
                )
                SELECT COALESCE(sr.session_id, '') as session_id,
                       COUNT(*) as total,
                       COUNT(DISTINCT sr.skill_id) as skills,
                       MIN(sr.read_at) as first_seen, MAX(sr.read_at) as last_seen,
                       pl.plan_filename as plan_filename,
                       COALESCE(tm.is_test, 0) as is_test,
                       tm.test_name as test_name
                FROM skill_reads sr
                LEFT JOIN plan_latest pl
                       ON pl.session_id = sr.session_id AND pl.rn = 1
                LEFT JOIN test_markers tm
                       ON tm.trace_id = sr.session_id
                {sessions_where}
                GROUP BY COALESCE(sr.session_id, '')
                ORDER BY last_seen DESC LIMIT 50
            """).fetchall()
            sessions = [dict(r) for r in sessions_rows]
        return page, stats, sessions
    finally:
        conn.close()


# ── MCP tool calls ────────────────────────────────────────────

def list_mcp_calls_page(
    *,
    tool_filter: Optional[str],
    session_filter: Optional[str],
    include_tests: bool,
    cursor_token: Optional[str],
    size: int,
) -> tuple[CursorPage, list[dict], list[dict]]:
    """Paginated MCP tool-call feed + first-page stats/sessions.

    Filters session_spans to `name LIKE 'tool.mcp__%'` and projects
    `tool_name`/`tool_input_keys` out of the JSON attributes blob.
    """
    from lib.orm.engine import get_connection

    test_exclusion = "" if include_tests else """trace_id NOT IN (
        SELECT DISTINCT trace_id FROM session_spans
        WHERE json_extract(attributes, '$.is_test') = 1
    )"""

    # Exclude live PENDING placeholders — the append-only store keeps a
    # pending tool span alongside its resolved twin, which would double-count
    # in the feed and stats until the merge drops it at read time.
    conditions = ["name LIKE 'tool.mcp__%'", "status_code != 'PENDING'"]
    params: list = []
    if tool_filter:
        conditions.append("json_extract(attributes, '$.tool_name') = ?")
        params.append(tool_filter)
    if session_filter:
        conditions.append("trace_id = ?")
        params.append(session_filter)
    if test_exclusion:
        conditions.append(test_exclusion)
    where_clause = " WHERE " + " AND ".join(conditions)

    base_sql = f"""
        SELECT id, trace_id as session_id, span_id,
               json_extract(attributes, '$.tool_name') as tool_name,
               json_extract(attributes, '$.tool_input_keys') as tool_input_keys,
               start_time, start_time as called_at, duration_ms
        FROM session_spans
        {where_clause}
    """

    conn = get_connection()
    try:
        page = keyset_page(
            conn, base_sql, params,
            order_cols=[("start_time", "DESC"), ("span_id", "DESC")],
            cursor_token=cursor_token, size=size,
        )

        stats: list[dict] = []
        sessions: list[dict] = []
        if cursor_token is None:
            base_conditions = ["name LIKE 'tool.mcp__%'", "status_code != 'PENDING'"]
            base_params: list = []
            if test_exclusion:
                base_conditions.append(test_exclusion)
            base_where = " WHERE " + " AND ".join(base_conditions)

            stats_rows = conn.execute(f"""
                SELECT json_extract(attributes, '$.tool_name') as tool_name,
                       COUNT(*) as total,
                       MAX(start_time) as last_seen
                FROM session_spans
                {base_where}
                GROUP BY tool_name ORDER BY last_seen DESC
            """, base_params).fetchall()
            stats = [dict(r) for r in stats_rows]

            session_conditions = ["session_spans.name LIKE 'tool.mcp__%'",
                                   "session_spans.status_code != 'PENDING'"]
            if test_exclusion:
                session_conditions.append(
                    test_exclusion.replace(
                        'trace_id NOT IN',
                        'session_spans.trace_id NOT IN',
                    )
                )
            session_where = " WHERE " + " AND ".join(session_conditions)

            sessions_rows = conn.execute(f"""
                WITH test_markers AS (
                    SELECT trace_id,
                           {_IS_TEST_CASE} AS is_test,
                           MAX(json_extract(attributes, '$.test_name')) AS test_name
                    FROM session_spans
                    WHERE {_IS_TEST_WHERE}
                       OR json_extract(attributes, '$.test_name') IS NOT NULL
                    GROUP BY trace_id
                )
                SELECT COALESCE(session_spans.trace_id, '') as session_id,
                       COUNT(*) as total,
                       COUNT(DISTINCT json_extract(session_spans.attributes, '$.tool_name')) as tools,
                       MIN(session_spans.start_time) as first_seen,
                       MAX(session_spans.start_time) as last_seen,
                       COALESCE(tm.is_test, 0) as is_test,
                       tm.test_name as test_name
                FROM session_spans
                LEFT JOIN test_markers tm
                       ON tm.trace_id = session_spans.trace_id
                {session_where}
                GROUP BY COALESCE(session_spans.trace_id, '')
                ORDER BY last_seen DESC LIMIT 50
            """).fetchall()
            sessions = [dict(r) for r in sessions_rows]
        return page, stats, sessions
    finally:
        conn.close()
# ── Session detail (read-only projection) ───────────────────

def fetch_session_projection(trace_id: str) -> tuple[list[dict], list[dict]]:
    """Read-only: fetch + graft-orphans + widen-envelopes.

    Returns (widened_spans, tree). Does not mutate the DB — callers
    that want the cleanup persisted use `materialize_session`.
    """
    from lib.orm.engine import get_connection
    from lib.trace.merge import merge_spans
    from lib.trace.projection import (
        _build_span_tree, _fetch_spans, _widen_envelopes,
    )

    conn = get_connection()
    try:
        raw = _fetch_spans(conn, trace_id)
        grafted = merge_spans(raw)
        widened = _widen_envelopes(grafted)
        tree = _build_span_tree(widened)
        return widened, tree
    finally:
        conn.close()


# Names that anchor a "turn" in the projection. Only real `prompt`
# spans are turn boundaries; `task.notification` spans (background-task
# completions) nest under the previous prompt rather than starting a
# new turn — see `_graft_orphans`.
_TURN_ANCHOR_NAMES = ('prompt',)
# Additive reload picks up turn anchors plus first-class boundary spans
# that legitimately arrive as roots without an enclosing prompt — chiefly
# `compact.pre`/`compact.post`, which fire on `/compact` (no
# UserPromptSubmit so no new prompt anchor), and session lifecycle
# markers. Narrower than "any parent_id IS NULL" on purpose: pre-graft
# orphan tool/response spans also have null parents and would otherwise
# pop into the live view as bogus roots before being grafted.
_AFTER_ID_ANCHOR_NAMES = (
    *_TURN_ANCHOR_NAMES,
    'compact.pre', 'compact.post',
    'session.start', 'session.end',
)


def fetch_session_paginated(
    trace_id: str,
    *,
    limit: int = 50,
    before_id: int | None = None,
    after_id: int | None = None,
) -> tuple[list[dict], list[dict], bool]:
    """Read-only: SQL-paginated variant of `fetch_session_projection`.

    Pages by *turn anchors* (prompt spans). The
    page sets the time window; all spans starting from the window's
    earliest anchor are fetched and run through the standard
    projection (graft → widen → tree). The projection is stateless
    within a chronological window, so the resulting tree fragment is
    correct.

    Cursors are mutually exclusive:
      - neither: latest `limit` anchors (initial mount).
      - `before_id`: next `limit` older anchors (scroll-up).
      - `after_id`: every anchor newer than `after_id` (additive reload).

    Returns `(widened_spans, tree, has_more_older)`.
    """
    if before_id is not None and after_id is not None:
        raise ValueError("before_id and after_id are mutually exclusive")
    if limit < 1:
        raise ValueError("limit must be >= 1")

    from lib.orm.engine import get_connection
    from lib.trace.merge import merge_spans
    from lib.trace.projection import (
        _build_span_tree, _widen_envelopes,
    )

    placeholders = ','.join('?' for _ in _TURN_ANCHOR_NAMES)
    conn = get_connection()
    try:
        # Step 1: page the turn anchors. For after_id, no LIMIT — we
        # want every anchor newer than the cursor (capped server-side
        # by a sane upper bound to defend against runaway growth).
        #
        # `OR status_code = 'PENDING'` re-includes the live promptlive-
        # placeholder on every additive reload even though its id is now
        # <= the cursor — without it the in-flight prompt would never
        # reach the live view until its real anchor lands on Stop. Scoped
        # by `name IN (_AFTER_ID_ANCHOR_NAMES)` (prompt + boundaries only),
        # so no pending tool/permission span leaks in. The cursor still
        # advances: the real anchor lands with a higher id and is picked
        # up by `id > ?`. The frontend prunes the stale placeholder when
        # the real anchor arrives, so there's no duplicate.
        if after_id is not None:
            after_placeholders = ','.join('?' for _ in _AFTER_ID_ANCHOR_NAMES)
            anchors = conn.execute(
                f"""
                SELECT id, start_time FROM session_spans
                WHERE trace_id = ?
                  AND name IN ({after_placeholders})
                  AND (id > ? OR status_code = 'PENDING')
                ORDER BY start_time ASC, id ASC
                LIMIT 500
                """,
                (trace_id, *_AFTER_ID_ANCHOR_NAMES, after_id),
            ).fetchall()
        elif before_id is not None:
            anchors = conn.execute(
                f"""
                SELECT id, start_time FROM session_spans
                WHERE trace_id = ?
                  AND name IN ({placeholders})
                  AND id < ?
                ORDER BY start_time DESC, id DESC
                LIMIT ?
                """,
                (trace_id, *_TURN_ANCHOR_NAMES, before_id, limit),
            ).fetchall()
        else:
            anchors = conn.execute(
                f"""
                SELECT id, start_time FROM session_spans
                WHERE trace_id = ?
                  AND name IN ({placeholders})
                ORDER BY start_time DESC, id DESC
                LIMIT ?
                """,
                (trace_id, *_TURN_ANCHOR_NAMES, limit),
            ).fetchall()

        if not anchors:
            return [], [], False, []

        # Step 2: window = [earliest_anchor_in_page, upper_bound).
        # For before_id, cap the upper bound at the cursor's
        # start_time so the page doesn't bleed in roots from
        # already-loaded later turns (the cursor itself and beyond
        # are owned by the caller's existing tree). For initial and
        # after_id pages, no upper bound — we want everything from
        # the window start onward.
        window_start = min(a['start_time'] for a in anchors)
        window_end = None
        if before_id is not None:
            cursor_row = conn.execute(
                "SELECT start_time FROM session_spans "
                "WHERE trace_id = ? AND id = ?",
                (trace_id, before_id),
            ).fetchone()
            if cursor_row:
                window_end = cursor_row['start_time']

        # Step 3: do we have any older anchors? Drives the
        # `↑ More history above` indicator on the frontend.
        if after_id is not None:
            # Reload doesn't care about older history — that's already
            # rendered. Caller passes in its existing has_more_older.
            has_more_older = False
        else:
            older = conn.execute(
                f"""
                SELECT 1 FROM session_spans
                WHERE trace_id = ?
                  AND name IN ({placeholders})
                  AND start_time < ?
                LIMIT 1
                """,
                (trace_id, *_TURN_ANCHOR_NAMES, window_start),
            ).fetchone()
            has_more_older = older is not None

        # Step 4: fetch every span in the window.
        if window_end is not None:
            raw_rows = conn.execute(
                """
                SELECT id, trace_id, span_id, parent_id, name, kind,
                       start_time, end_time, duration_ms, attributes,
                       status_code, status_message, turn_uuid
                FROM session_spans
                WHERE trace_id = ?
                  AND start_time >= ?
                  AND start_time < ?
                ORDER BY start_time ASC, id ASC
                """,
                (trace_id, window_start, window_end),
            ).fetchall()
        else:
            raw_rows = conn.execute(
                """
                SELECT id, trace_id, span_id, parent_id, name, kind,
                       start_time, end_time, duration_ms, attributes,
                       status_code, status_message, turn_uuid
                FROM session_spans
                WHERE trace_id = ? AND start_time >= ?
                ORDER BY start_time ASC, id ASC
                """,
                (trace_id, window_start),
            ).fetchall()
        raw = [
            {**dict(r), 'attributes': json.loads(r['attributes'])}
            for r in raw_rows
        ]

        # Step 5: standard projection on the windowed subset. merge_spans
        # dedups coexisting placeholder/pending rows (append-only store) then
        # runs the deterministic reparent ladder. Pass the GLOBAL max prompt id
        # so a stray prompt placeholder still drops when it happens to be the
        # newest anchor *within this older window* (window-local max alone
        # would keep it — see merge._drop_stale_blockers).
        ceiling_row = conn.execute(
            "SELECT MAX(id) FROM session_spans "
            "WHERE trace_id = ? AND name = 'prompt'",
            (trace_id,),
        ).fetchone()
        prompt_ceiling = ceiling_row[0] if ceiling_row else None
        grafted = merge_spans(raw, prompt_id_ceiling=prompt_ceiling)
        widened = _widen_envelopes(grafted)
        tree = _build_span_tree(widened)
        # Placeholder/superseded rows the merge dropped from this window. The
        # append-only store keeps them on disk, so the client's append-only
        # `session.spans` must prune exactly these or the conversation cards
        # show a duplicate (placeholder + resolved). Computed as raw−merged so
        # it's robust to id ordering (a retired row can sort below survivors).
        grafted_ids = {s['span_id'] for s in grafted}
        retired_ids = [s['span_id'] for s in raw if s['span_id'] not in grafted_ids]
        return widened, tree, has_more_older, retired_ids
    finally:
        conn.close()
def fetch_tool_token_rollup(trace_id: str) -> tuple[list[dict], dict]:
    """Aggregate per-tool token cost for one session.

    Reads `session_spans` directly so the rollup works regardless of
    whether the trace UI loaded the tree shallow or full. Aggregates
    by `attributes.tool_name` (falling back to the span name) so MCP
    tools group under their full `mcp__server__tool` name.

    Returns (per_tool_rows, totals) where totals carries the session-
    level numbers needed to compute the untagged remainder.
    """
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        # assistant_response spans are bucketed under a synthetic
        # `assistant_text` tool name so the chip can show prose tokens
        # alongside Bash/Read/etc. tools. assistant.thinking spans get
        # their own `assistant_thinking` bucket so extended-thinking
        # tokens (estimated from the captured thinking_text) come out
        # of the untagged remainder. Both span kinds only carry
        # output_tokens — the API doesn't expose per-block accounting
        # on the input side.
        rows = conn.execute("""
            SELECT
                CASE WHEN name = 'assistant_response' THEN 'assistant_text'
                     WHEN name = 'assistant.thinking' THEN 'assistant_thinking'
                     ELSE COALESCE(json_extract(attributes, '$.tool_name'),
                                   substr(name, 6))
                END AS tool,
                COUNT(*)         AS calls,
                SUM(COALESCE(input_tokens, 0))  AS in_tok,
                SUM(COALESCE(output_tokens, 0)) AS out_tok,
                SUM(COALESCE(image_tokens, 0))  AS img_tok,
                SUM(COALESCE(cost_usd, 0))      AS cost
            FROM session_spans
            WHERE trace_id = ?
              AND (name LIKE 'tool.%' OR name IN ('assistant_response', 'assistant.thinking'))
              AND (input_tokens IS NOT NULL OR output_tokens IS NOT NULL)
            GROUP BY tool
            ORDER BY (SUM(COALESCE(input_tokens,0)) + SUM(COALESCE(output_tokens,0))) DESC
        """, (trace_id,)).fetchall()
        rollup = [
            {
                'name': r[0] or '',
                'calls': int(r[1] or 0),
                'input_tokens': int(r[2] or 0),
                'output_tokens': int(r[3] or 0),
                'image_tokens': int(r[4] or 0),
                'cost_usd': float(r[5] or 0.0),
            }
            for r in rows
        ]
        sess_row = conn.execute(
            "SELECT input_tokens, output_tokens FROM sessions WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
    finally:
        conn.close()

    attributed_in = sum(r['input_tokens'] for r in rollup)
    attributed_out = sum(r['output_tokens'] for r in rollup)
    attributed_cost = sum(r['cost_usd'] for r in rollup)
    session_in = int((sess_row[0] if sess_row else 0) or 0)
    session_out = int((sess_row[1] if sess_row else 0) or 0)
    totals = {
        'attributed_input_tokens': attributed_in,
        'attributed_output_tokens': attributed_out,
        'attributed_cost_usd': attributed_cost,
        'session_input_tokens': session_in,
        'session_output_tokens': session_out,
        'untagged_input_tokens': max(0, session_in - attributed_in),
        'untagged_output_tokens': max(0, session_out - attributed_out),
    }
    return rollup, totals


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


def fetch_turn_usage(trace_id: str) -> list[dict]:
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

    First-turn interval starts at `-inf` so any span preceding the
    first turn's timestamp gets attributed there rather than dropped.
    """
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT turn_uuid, turn_index, timestamp, model,
                   input_tokens, output_tokens,
                   cache_read_tokens, cache_creation_tokens,
                   context_used_tokens, request_id, effort_level
            FROM turn_usage
            WHERE trace_id = ?
            ORDER BY timestamp ASC, turn_index ASC
        """, (trace_id,)).fetchall()
        turns = [dict(r) for r in rows]

        if not turns:
            return turns

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
    finally:
        conn.close()

    turn_bounds = [_to_utc(t.get('timestamp')) for t in turns]

    import json as _json

    # Build parallel buckets, one per turn. The i-th bucket receives
    # spans whose start_time is in (turn_bounds[i-1], turn_bounds[i]].
    buckets: list[list[dict]] = [[] for _ in turns]
    for sr in span_rows:
        name = sr['name']
        if name in _TURN_SPAN_EXCLUDE:
            continue
        dt = _to_utc(sr['start_time'])
        if dt is None:
            continue
        # Find the first turn whose bound >= dt; that turn owns this span.
        idx = None
        for i, bound in enumerate(turn_bounds):
            if bound is not None and dt <= bound:
                idx = i
                break
        if idx is None:
            # Span after the last turn's timestamp — drop it. These
            # would be in-flight spans for the current turn that hasn't
            # finalised yet; the next turn_usage fire will adopt them.
            continue
        try:
            attrs = _json.loads(sr['attributes']) if sr['attributes'] else {}
        except (TypeError, ValueError):
            attrs = {}
        tool_name = attrs.get('tool_name') if isinstance(attrs.get('tool_name'), str) else None
        buckets[idx].append({
            'span_id': sr['span_id'],
            'name': name,
            'start_time': sr['start_time'],
            'tool_name': tool_name,
        })

    for turn, bucket in zip(turns, buckets):
        counts: dict[str, int] = {}
        for s in bucket:
            key = s['tool_name'] or s['name']
            counts[key] = counts.get(key, 0) + 1
        summary = sorted(
            ({'name': k, 'count': v} for k, v in counts.items()),
            key=lambda e: (-e['count'], e['name']),
        )
        turn['span_refs'] = bucket
        turn['span_count'] = len(bucket)
        turn['tool_summary'] = summary
        turn['is_server_side'] = turn.get('turn_uuid') in server_side_uuids

    return turns
