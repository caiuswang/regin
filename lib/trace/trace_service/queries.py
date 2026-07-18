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

from lib.trace.workflow_labels import attach_workflow_agent_attrs
from lib.utils.pagination import CursorPage, keyset_page


# Test-session exclusion, parameterised by the caller's session-id column.
#
# This reads the precomputed `sessions.is_test` rather than re-deriving the
# marker from span attributes. The two are the same value: ingest latches
# `is_test = MAX(sessions.is_test, excluded.is_test)` from the very span
# attribute the old subquery scanned. Re-deriving it cost a full
# `json_extract` scan of session_spans (~0.5s at 336k rows) on every feed,
# stats and sessions query — three per first page — to reproduce a column
# that was already written at ingest time.
_TEST_EXCLUSION = (
    "{col} NOT IN (SELECT trace_id FROM sessions WHERE is_test = 1)"
)


# `skill_reads.read_at` is written as `datetime.now().isoformat()` — local
# time, `T`-separated. SQLite's `datetime('now')` is UTC and space-separated,
# so a naive comparison is wrong twice over: it shifts the window by the UTC
# offset, and on the boundary day the `T` (0x54) sorts above the space (0x20),
# pulling in reads from earlier that day. Normalise the cutoff to match.
_LOCAL_CUTOFF = "replace(datetime('now','localtime','-{days} days'),' ','T')"


def _skill_roi_rows(conn, where: str, params: list) -> list[dict]:
    """Per-skill adoption rollup: how a skill was reached, how far it spread,
    and whether its use is growing.

    Split by `source` because the three are not interchangeable signals — a
    skill that is only ever `read` is being pulled in as reference material,
    while `invoke`/`launch` mean it was deliberately run.
    """
    w0 = _LOCAL_CUTOFF.format(days=7)
    w14 = _LOCAL_CUTOFF.format(days=14)
    rows = conn.execute(f"""
        SELECT skill_id,
               COUNT(*) AS total,
               SUM(source = 'invoke') AS invokes,
               SUM(source = 'read')   AS reads,
               SUM(source = 'launch') AS launches,
               COUNT(DISTINCT session_id) AS sessions,
               SUM(read_at >= {w0}) AS recent,
               SUM(read_at >= {w14} AND read_at < {w0}) AS prior,
               MIN(read_at) AS first_seen,
               MAX(read_at) AS last_seen
        FROM skill_reads
        {where}
        GROUP BY skill_id
        ORDER BY recent DESC, total DESC
    """, params).fetchall()
    return [dict(r) for r in rows]


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

    def build_where(prefix: str = "") -> tuple[str, list]:
        """Every card on the page answers for the same filtered set.

        The summaries used to apply only the test exclusion, so an active
        `skill=` chip narrowed the event feed while the summary cards kept
        reporting totals for all skills — two different populations shown
        side by side under one filter.
        """
        conditions: list[str] = []
        params: list = []
        if skill_filter:
            conditions.append(f"{prefix}skill_id = ?")
            params.append(skill_filter)
        if session_filter:
            conditions.append(f"{prefix}session_id = ?")
            params.append(session_filter)
        if not include_tests:
            conditions.append(_TEST_EXCLUSION.format(col=f"{prefix}session_id"))
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        return where, params

    where, params = build_where()
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
            stats = _skill_roi_rows(conn, where, params)

            sessions_where, sessions_params = build_where("sr.")
            sessions_rows = conn.execute(f"""
                WITH plan_latest AS (
                    SELECT session_id, plan_filename,
                           ROW_NUMBER() OVER (
                               PARTITION BY session_id ORDER BY started_at DESC
                           ) AS rn
                    FROM plan_sessions
                )
                SELECT COALESCE(sr.session_id, '') as session_id,
                       COUNT(*) as total,
                       COUNT(DISTINCT sr.skill_id) as skills,
                       MIN(sr.read_at) as first_seen, MAX(sr.read_at) as last_seen,
                       pl.plan_filename as plan_filename,
                       COALESCE(s.is_test, 0) as is_test,
                       s.test_name as test_name
                FROM skill_reads sr
                LEFT JOIN plan_latest pl
                       ON pl.session_id = sr.session_id AND pl.rn = 1
                LEFT JOIN sessions s ON s.trace_id = sr.session_id
                {sessions_where}
                GROUP BY COALESCE(sr.session_id, '')
                ORDER BY last_seen DESC LIMIT 50
            """, sessions_params).fetchall()
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

    test_exclusion = "" if include_tests else _TEST_EXCLUSION.format(col="trace_id")

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
                    _TEST_EXCLUSION.format(col="session_spans.trace_id")
                )
            session_where = " WHERE " + " AND ".join(session_conditions)

            sessions_rows = conn.execute(f"""
                SELECT COALESCE(session_spans.trace_id, '') as session_id,
                       COUNT(*) as total,
                       COUNT(DISTINCT json_extract(session_spans.attributes, '$.tool_name')) as tools,
                       MIN(session_spans.start_time) as first_seen,
                       MAX(session_spans.start_time) as last_seen,
                       COALESCE(s.is_test, 0) as is_test,
                       s.test_name as test_name
                FROM session_spans
                LEFT JOIN sessions s ON s.trace_id = session_spans.trace_id
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
        grafted = merge_spans(
            raw, prompt_id_ceiling=_prompt_ceiling(conn, trace_id),
            session_activity=_session_activity(conn, trace_id),
        )
        widened = _widen_envelopes(grafted)
        _attach_compaction_reclaim(conn, trace_id, widened)
        _attach_subagent_impact(widened)
        _attach_prompt_expansions(trace_id, widened)
        attach_workflow_agent_attrs(trace_id, widened, conn)
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


def _page_anchors(conn, trace_id, *, limit, before_id, after_id):
    """Step 1: page the turn anchors. Returns rows of `(id, start_time)`.

    For after_id, no LIMIT — we want every anchor newer than the cursor
    (capped server-side by a sane upper bound to defend against runaway
    growth).

    `OR status_code = 'PENDING'` re-includes the live promptlive-
    placeholder on every additive reload even though its id is now
    <= the cursor — without it the in-flight prompt would never
    reach the live view until its real anchor lands on Stop. Scoped
    by `name IN (_AFTER_ID_ANCHOR_NAMES)` (prompt + boundaries only),
    so no pending tool/permission span leaks in. The cursor still
    advances: the real anchor lands with a higher id and is picked
    up by `id > ?`. The frontend prunes the stale placeholder when
    the real anchor arrives, so there's no duplicate.
    """
    placeholders = ','.join('?' for _ in _TURN_ANCHOR_NAMES)
    if after_id is not None:
        after_placeholders = ','.join('?' for _ in _AFTER_ID_ANCHOR_NAMES)
        return conn.execute(
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
    if before_id is not None:
        return conn.execute(
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
    return conn.execute(
        f"""
        SELECT id, start_time FROM session_spans
        WHERE trace_id = ?
          AND name IN ({placeholders})
        ORDER BY start_time DESC, id DESC
        LIMIT ?
        """,
        (trace_id, *_TURN_ANCHOR_NAMES, limit),
    ).fetchall()


def _window_end_for(conn, trace_id, before_id):
    """Step 2: upper bound for a before_id page.

    Cap the upper bound at the cursor's start_time so the page doesn't
    bleed in roots from already-loaded later turns (the cursor itself and
    beyond are owned by the caller's existing tree). For initial and
    after_id pages, no upper bound — we want everything from the window
    start onward.
    """
    if before_id is None:
        return None
    cursor_row = conn.execute(
        "SELECT start_time FROM session_spans "
        "WHERE trace_id = ? AND id = ?",
        (trace_id, before_id),
    ).fetchone()
    return cursor_row['start_time'] if cursor_row else None


def _compute_has_more_older(conn, trace_id, after_id, window_start):
    """Step 3: do we have any older anchors? Drives the
    `↑ More history above` indicator on the frontend.

    Reload (after_id) doesn't care about older history — that's already
    rendered. Caller passes in its existing has_more_older.
    """
    if after_id is not None:
        return False
    placeholders = ','.join('?' for _ in _TURN_ANCHOR_NAMES)
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
    return older is not None


def _fetch_window_rows(conn, trace_id, window_start, window_end):
    """Step 4: fetch every span in the window, with attributes decoded."""
    if window_end is not None:
        raw_rows = conn.execute(
            """
            SELECT id, trace_id, span_id, parent_id, name, kind,
                   start_time, end_time, duration_ms, attributes,
                   status_code, status_message, turn_uuid,
                   output_tokens, input_tokens, image_tokens, cost_usd,
                   source_prompt_id, source
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
                   status_code, status_message, turn_uuid,
                   output_tokens, input_tokens, image_tokens, cost_usd,
                   source_prompt_id, source
            FROM session_spans
            WHERE trace_id = ? AND start_time >= ?
            ORDER BY start_time ASC, id ASC
            """,
            (trace_id, window_start),
        ).fetchall()
    return [
        {**dict(r), 'attributes': json.loads(r['attributes'])}
        for r in raw_rows
    ]


def _prompt_ceiling(conn, trace_id):
    """The GLOBAL max MAIN-agent prompt id, threaded into merge_spans so a stray
    prompt placeholder still drops when it happens to be the newest anchor
    *within an older window* (window-local max alone would keep it — see
    merge._drop_stale_blockers). Subagent launch prompts (`prompt-sa-`,
    agent_id set) are excluded: they are not main turn anchors, so a subagent
    prompt with a higher id must never raise the cutoff and drop the user's
    live main placeholder."""
    from lib.trace.pending_spans import AGENT_ID_SQL
    ceiling_row = conn.execute(
        "SELECT MAX(id) FROM session_spans "
        f"WHERE trace_id = ? AND name = 'prompt' AND {AGENT_ID_SQL} IS NULL",
        (trace_id,),
    ).fetchone()
    return ceiling_row[0] if ceiling_row else None


def _session_activity(conn, trace_id):
    """{'status', 'last_seen'} for the session, threaded into merge_spans so
    the stuck-pending demotion can tell an active session (never demote) from
    an inactive/ended one (demote old blockers). None when the row is absent
    (a not-yet-summarised trace) — demotion then falls back to the
    same-agent-moved-on path alone."""
    row = conn.execute(
        "SELECT status, last_seen FROM sessions WHERE trace_id = ?",
        (trace_id,),
    ).fetchone()
    if row is None:
        return None
    return {'status': row['status'], 'last_seen': row['last_seen']}


def _compute_retired_ids(raw, grafted):
    """Placeholder/superseded rows the merge dropped from this window. The
    append-only store keeps them on disk, so the client's append-only
    `session.spans` must prune exactly these or the conversation cards
    show a duplicate (placeholder + resolved). Computed as raw−merged so
    it's robust to id ordering (a retired row can sort below survivors)."""
    grafted_ids = {s['span_id'] for s in grafted}
    return [s['span_id'] for s in raw if s['span_id'] not in grafted_ids]


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
    from lib.trace.wakeup_links import annotate_wakeup_resumes

    conn = get_connection()
    try:
        # Step 1: page the turn anchors.
        anchors = _page_anchors(
            conn, trace_id,
            limit=limit, before_id=before_id, after_id=after_id,
        )
        if not anchors:
            return [], [], False, []

        # Step 2: window = [earliest_anchor_in_page, upper_bound).
        window_start = min(a['start_time'] for a in anchors)
        window_end = _window_end_for(conn, trace_id, before_id)

        # Step 3: do we have any older anchors?
        has_more_older = _compute_has_more_older(
            conn, trace_id, after_id, window_start,
        )

        # Step 4: fetch every span in the window.
        raw = _fetch_window_rows(conn, trace_id, window_start, window_end)

        # Step 5: standard projection on the windowed subset. merge_spans
        # dedups coexisting placeholder/pending rows (append-only store) then
        # runs the deterministic reparent ladder.
        grafted = merge_spans(
            raw, prompt_id_ceiling=_prompt_ceiling(conn, trace_id),
            session_activity=_session_activity(conn, trace_id),
        )
        widened = _widen_envelopes(grafted)
        _attach_compaction_reclaim(conn, trace_id, widened)
        _attach_subagent_impact(widened)
        attach_workflow_agent_attrs(trace_id, widened, conn)
        annotate_wakeup_resumes(widened)
        tree = _build_span_tree(widened)
        retired_ids = _compute_retired_ids(raw, grafted)
        return widened, tree, has_more_older, retired_ids
    finally:
        conn.close()
def _command_str(tool_input: object) -> str | None:
    """Pull the command/pattern string out of a stored `tool_input`
    attribute. It may be a dict, a JSON string, or a Python-repr string
    (the Bash hook stored some as `str(dict)`, which isn't valid JSON)."""
    import ast
    val = tool_input
    if isinstance(val, str):
        parsed = None
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(val)
            except (ValueError, SyntaxError, TypeError):
                continue
            break
        val = parsed
    if isinstance(val, dict):
        return val.get('command') or val.get('pattern') or val.get('glob')
    return None


_FILE_TOOLS = ('Read', 'Edit', 'Write', 'NotebookEdit')
_CMD_TOOLS = ('Bash', 'Grep', 'Glob')
_AGENT_TOOLS = ('Agent', 'Task')


def _nonempty_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _file_target(attrs: dict) -> tuple[str, str] | None:
    path = _nonempty_str(attrs.get('file_path') or attrs.get('notebook_path'))
    return (path, path.rsplit('/', 1)[-1]) if path else None


def _cmd_target(attrs: dict) -> tuple[str, str] | None:
    """`(full, label)` for a command-style tool (Bash/Grep/Glob), or None.

    The command/pattern lives in different attributes across capture paths:
    the live hook path (`post_tool_trace`) stores `command`/`command_preview`
    for Bash and `pattern` for Grep/Glob, while the workflow-ingest path stores
    the raw `tool_input` dict. Reading only `tool_input` (the old behaviour)
    left every hook-captured Bash/Grep/Glob span with no drill-down target, so
    the per-tool rollup's jump-to-span button was permanently disabled for them.
    """
    cmd = (_nonempty_str(attrs.get('command'))
           or _command_str(attrs.get('tool_input'))
           or _nonempty_str(attrs.get('command_preview'))
           or _nonempty_str(attrs.get('pattern'))
           or _nonempty_str(attrs.get('glob'))
           or _nonempty_str(attrs.get('query')))
    flat = ' '.join((cmd or '').split())
    if not flat:
        return None
    return flat, (flat[:160] + '…' if len(flat) > 160 else flat)


def _agent_target(attrs: dict) -> tuple[str, str] | None:
    desc = _nonempty_str(attrs.get('description') or attrs.get('subagent_type'))
    return (desc, desc) if desc else None


def _span_target(attrs: dict) -> tuple[str, str] | None:
    """`(full_target, display_label)` a tool call drills into, or None when
    the tool has no meaningful per-call target. file_path for file tools,
    the command for Bash/Grep/Glob, the description for subagents."""
    tool = attrs.get('tool_name')
    if tool in _FILE_TOOLS:
        return _file_target(attrs)
    if tool in _CMD_TOOLS:
        return _cmd_target(attrs)
    if tool in _AGENT_TOOLS:
        return _agent_target(attrs)
    return None


def _clean_target_cell(c: dict) -> dict:
    """Public shape of a drill-down target — drops the internal peak tracker."""
    return {'target': c['target'], 'label': c['label'], 'tokens': c['tokens'],
            'calls': c['calls'], 'span_id': c['span_id']}


def _tool_target_breakdown(conn, trace_id: str, per_tool: int = 8) -> dict:
    """Per-tool top targets by token cost: `{tool_key: [{target, label,
    tokens, calls, span_id}, ...]}`, top `per_tool` each. `tool_key` matches
    `fetch_tool_token_rollup`'s row name (`attributes.tool_name`), so the UI
    can hang each tool's drill-down off its rollup row. Token cost is
    input+output — the SAME total the rollup row shows — so the per-target
    sums reconcile with the tool subtotal. (For Read/Bash that's ~all result
    tokens; for Edit/Write the bulk is the OUTPUT — the edit/write content
    the model emitted — which input-only would have hidden.) `span_id` is the
    single most expensive call for that target, for jump-to-span."""
    rows = conn.execute(
        "SELECT json_extract(attributes, '$.tool_name') AS tool, "
        "span_id, attributes, "
        "COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) AS tok "
        "FROM session_spans "
        "WHERE trace_id = ? AND name LIKE 'tool.%' "
        "AND status_code != 'PENDING' "
        "AND (input_tokens > 0 OR output_tokens > 0)",
        (trace_id,),
    ).fetchall()
    agg: dict = {}
    for r in rows:
        try:
            attrs = json.loads(r['attributes'] or '{}')
        except (ValueError, TypeError):
            continue
        target = _span_target(attrs)
        if not r['tool'] or target is None:
            continue
        full, label = target
        tok = int(r['tok'] or 0)
        cell = agg.setdefault(r['tool'], {}).setdefault(
            full, {'target': full, 'label': label, 'tokens': 0, 'calls': 0,
                   'span_id': None, 'peak': -1})
        cell['tokens'] += tok
        cell['calls'] += 1
        if tok > cell['peak']:
            cell['peak'], cell['span_id'] = tok, r['span_id']
    return {
        tool: [_clean_target_cell(c) for c in
               sorted(cells.values(), key=lambda c: -c['tokens'])[:per_tool]]
        for tool, cells in agg.items()
    }


def _rollup_row(r, targets_by_tool: dict) -> dict:
    """Build one per-tool rollup row, hanging its drill-down targets off the
    same `tool` key the breakdown was aggregated under."""
    name = r[0] or ''
    return {
        'name': name,
        'calls': int(r[1] or 0),
        'input_tokens': int(r[2] or 0),
        'output_tokens': int(r[3] or 0),
        'image_tokens': int(r[4] or 0),
        'cost_usd': float(r[5] or 0.0),
        'targets': targets_by_tool.get(name, []),
    }


def _session_bill_cost(conn, trace_id: str) -> dict:
    """Per-bucket USD cost for the full recorded session bill, keyed
    `{input, output, cache_read, cache_write}`.

    Summed per-turn from `turn_usage`, each turn priced at its own context
    tier — mirroring ingest's `_insert_one_turn_row` — so the bucket costs
    reconcile to `sessions.cost_usd`. It can't be derived from the `sessions`
    aggregate row: the >200K tier rate is per-request, and cache reads bill
    at ~1/10 the input rate, so the cost split (≈ even thirds across
    cache-read / cache-write / output here) looks nothing like the token
    split (≈ 90% cache-read). 'Tokens by tool' attributes only output by
    activity; this is the dollar context that view omits. Zeros when no turn
    carries a model the catalogue knows.
    """
    from lib.tokens.pricing import TokenBreakdown, cost_components

    out = {'input': 0.0, 'output': 0.0, 'cache_read': 0.0, 'cache_write': 0.0}
    rows = conn.execute(
        "SELECT model, input_tokens, output_tokens, cache_read_tokens, "
        "cache_creation_tokens, context_used_tokens FROM turn_usage "
        "WHERE trace_id = ?",
        (trace_id,),
    ).fetchall()
    for r in rows:
        comps = cost_components(
            r['model'],
            TokenBreakdown(
                input_tokens=int(r['input_tokens'] or 0),
                output_tokens=int(r['output_tokens'] or 0),
                cache_read_tokens=int(r['cache_read_tokens'] or 0),
                cache_creation_tokens=int(r['cache_creation_tokens'] or 0),
            ),
            context_tokens=int(r['context_used_tokens'] or 0),
        )
        if comps:
            for k in out:
                out[k] += comps[k]
    return out


def _rollup_totals(rollup: list[dict], sess_row, bill_cost: dict,
                   subagent_cost: float = 0.0,
                   subagent_tokens: int = 0) -> dict:
    """Assemble the session-level totals payload for `fetch_tool_token_rollup`.

    `attributed_*` sum the per-tool rows; `session_*` come from the recorded
    `sessions` aggregate (main model only); `untagged_*` is the output the
    rollup couldn't pin to a tool; the `*_cost_usd` quartet is the
    per-turn-priced dollar split (`_session_bill_cost`); `subagent_*` is the
    server-side sub-model spend (the advisor) that `sessions.cost_usd`
    excludes. `total_spend_*` = main bill + sub-agent is true spend — the
    honest footer total and the "$X of $Y" denominator. Kept out of the
    caller so it stays under the cyclomatic-complexity grade.
    """
    def _cell(key: str) -> int:
        return int((sess_row[key] if sess_row else 0) or 0)

    attributed_in = sum(r['input_tokens'] for r in rollup)
    attributed_out = sum(r['output_tokens'] for r in rollup)
    session_in = _cell('input_tokens')
    session_out = _cell('output_tokens')
    session_cache_read = _cell('cache_read_tokens')
    session_cache_write = _cell('cache_creation_tokens')
    session_total = (session_in + session_out
                     + session_cache_read + session_cache_write)
    session_cost = float((sess_row['cost_usd'] if sess_row else 0) or 0.0)
    return {
        'attributed_input_tokens': attributed_in,
        'attributed_output_tokens': attributed_out,
        'attributed_cost_usd': sum(r['cost_usd'] for r in rollup),
        'session_input_tokens': session_in,
        'session_output_tokens': session_out,
        'session_cache_read_tokens': session_cache_read,
        'session_cache_creation_tokens': session_cache_write,
        'session_total_tokens': session_total,
        'session_cost_usd': session_cost,
        'untagged_input_tokens': max(0, session_in - attributed_in),
        'untagged_output_tokens': max(0, session_out - attributed_out),
        # Per-bucket dollar split of the main-model bill (cache reads bill
        # ~10x cheaper than fresh input, so the cost story ≠ the token story).
        'input_cost_usd': bill_cost['input'],
        'output_cost_usd': bill_cost['output'],
        'cache_read_cost_usd': bill_cost['cache_read'],
        'cache_write_cost_usd': bill_cost['cache_write'],
        # Server-side sub-model spend (advisor), excluded from session_cost_usd.
        'subagent_cost_usd': subagent_cost,
        'subagent_tokens': subagent_tokens,
        'total_spend_usd': session_cost + subagent_cost,
        'total_spend_tokens': session_total + subagent_tokens,
    }


def fetch_tool_token_rollup(trace_id: str) -> tuple[list[dict], dict]:
    """Aggregate per-tool token cost for one session.

    Reads `session_spans` directly so the rollup works regardless of
    whether the trace UI loaded the tree shallow or full. Aggregates
    by `attributes.tool_name` (falling back to the span name) so MCP
    tools group under their full `mcp__server__tool` name.

    Returns (per_tool_rows, totals). `totals` carries the session-level
    numbers the frontend needs to put the attribution in context: the
    recorded `session_*` token aggregate (main model, incl. cache
    read/write), the per-bucket `*_cost_usd` dollar split
    (`_session_bill_cost`), the `subagent_*` server-side sub-model spend
    (the advisor) that `sessions.cost_usd` excludes, the `total_spend_*`
    (main bill + sub-agent = true spend) used as the "$X of $Y" denominator
    and footer total, and the `untagged_*` output remainder — so the panel
    reconciles to true spend instead of presenting attributed tokens as the
    whole.
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
        breakdown = _tool_target_breakdown(conn, trace_id)
        rollup = [_rollup_row(r, breakdown) for r in rows]
        sess_row = conn.execute(
            "SELECT input_tokens, output_tokens, cache_read_tokens, "
            "cache_creation_tokens, cost_usd FROM sessions WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        bill_cost = _session_bill_cost(conn, trace_id)
        # Sub-agent spend that `sessions.cost_usd` (main transcript only)
        # omits, from two sources billed on a separate channel:
        #  * server-side sub-models (the Kimi advisor; web_search / web_fetch)
        #    whose cost lands on the span but never in turn_usage; and
        #  * Claude Task-tool *and* workflow subagents, whose isolated
        #    transcripts (`subagents/[workflows/<wf>/]agent-*.jsonl`)
        #    reconcile_claude_subagents totals onto each `subagent.stop` marker.
        # Folding both in lets the footer total reflect TRUE spend rather than
        # the main-model-only bill, and keeps the "$X of $Y" numerator a real
        # subset of its denominator.
        subagent = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS cost, "
            "COALESCE(SUM(COALESCE(input_tokens, 0) "
            "+ COALESCE(output_tokens, 0)), 0) AS tokens "
            "FROM session_spans WHERE trace_id = ? "
            "AND (json_extract(attributes, '$.server_side') = 1 "
            "     OR (name = 'subagent.stop' AND cost_usd IS NOT NULL))",
            (trace_id,),
        ).fetchone()
    finally:
        conn.close()

    return rollup, _rollup_totals(
        rollup, sess_row, bill_cost,
        subagent_cost=float(subagent['cost']),
        subagent_tokens=int(subagent['tokens']),
    )


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


def _reclaimed_for_pair(pre_at, post_at, turns: list[tuple]) -> int | None:
    """Tokens a single `/compact` freed: the context used by the last turn
    BEFORE `compact.pre` minus the context used by the first turn AFTER
    `compact.post`. `turns` is `[(utc_dt, context_used_tokens), …]` sorted
    ascending. Returns None when either bracket turn is missing (e.g. the
    boundary just landed and no post-turn exists yet) or the delta would be
    non-positive (no garbage numbers).

    A boundary first served without a post-turn re-acquires the value on a
    later poll: this is recomputed on every read, and the live tail's
    `mergeLoadedSpans` overwrites the cached span's attributes by span_id
    when the fresh fetch carries a non-empty attributes dict."""
    before = next(
        (ctx for ts, ctx in reversed(turns) if ts and ts <= pre_at and ctx),
        None,
    )
    after = next(
        (ctx for ts, ctx in turns if ts and ts >= post_at and ctx),
        None,
    )
    if before is None or after is None:
        return None
    delta = before - after
    return delta if delta > 0 else None


def _attach_compaction_reclaim(conn, trace_id: str, spans: list[dict]) -> None:
    """Stamp `attributes.reclaimed_tokens` onto every `compact.post` span
    in `spans` whose reclaim delta is computable.

    Pure serve-time derivation: the compact boundary spans carry no token
    payload, but `turn_usage.context_used_tokens` is recorded per turn. We
    pair each `compact.post` with its immediately preceding `compact.pre`
    (boundaries sorted by time) and read the bracketing turns' context use.

    Mutates the shared `attributes` dict in place so both the widened span
    list and the tree (which shallow-copies spans but shares `attributes`
    by reference) surface the value. Queried full-trace, not from the
    passed-in window, because a paginated window may not load the
    bracketing turns or the matching `compact.pre` as spans."""
    posts = [s for s in spans if s.get('name') == 'compact.post']
    if not posts:
        return
    pres = conn.execute(
        "SELECT start_time FROM session_spans "
        "WHERE trace_id = ? AND name = 'compact.pre' "
        "ORDER BY start_time ASC",
        (trace_id,),
    ).fetchall()
    pre_times = sorted(
        t for t in (_to_utc(r['start_time']) for r in pres) if t
    )
    turn_rows = conn.execute(
        "SELECT timestamp, context_used_tokens FROM turn_usage "
        "WHERE trace_id = ? ORDER BY timestamp ASC",
        (trace_id,),
    ).fetchall()
    turns = [(_to_utc(r['timestamp']), r['context_used_tokens']) for r in turn_rows]
    for post in posts:
        post_at = _to_utc(post.get('start_time'))
        if post_at is None:
            continue
        # Pair with the latest compact.pre at/before this post.
        pre_at = next((t for t in reversed(pre_times) if t <= post_at), None)
        if pre_at is None:
            continue
        reclaimed = _reclaimed_for_pair(pre_at, post_at, turns)
        if reclaimed is not None:
            post.setdefault('attributes', {})['reclaimed_tokens'] = reclaimed


def _enclosing_prompt_id(span: dict, by_id: dict) -> str | None:
    """Walk `parent_id` up to the nearest enclosing `prompt` span_id (or
    None). Cycle-guarded; the spans are already grafted, so a `tool.Agent`
    rises through its turn anchor and a `subagent.start` through the prompt
    it was re-parented under."""
    cur: dict | None = span
    seen: set[str] = set()
    while cur is not None and cur['span_id'] not in seen:
        if cur.get('name') == 'prompt':
            return cur['span_id']
        seen.add(cur['span_id'])
        cur = by_id.get(cur.get('parent_id'))
    return None


def _attach_subagent_impact(spans: list[dict]) -> None:
    """Stamp `attributes.main_session_impact_tokens` onto each
    `subagent.start` whose result we can unambiguously attribute.

    "Main-session impact" = the tokens the subagent's result text added back
    into the PARENT context, captured as the `input_tokens` of the matching
    `tool.Agent` launch span (the tool_result fed to the next parent turn).
    `tool.Agent` and `subagent.start` share no id; we correlate by enclosing
    prompt and only stamp when that prompt contains exactly ONE of each — a
    parallel fan-out (>1) can't be ordered safely (`tool.Agent.start_time` is
    completion order, `subagent.start` is start order), so per-subagent
    attribution there would be a guess. Sparse by design: `tool.Agent` only
    carries `input_tokens` once `ingest_tool_attribution` enriched it, so the
    chip shows on the turns where that data exists and is hidden elsewhere.

    Pure serve-time derivation; mutates the shared `attributes` dict in place
    (the tree shares it by reference). No DB, no schema change."""
    by_id = {s['span_id']: s for s in spans}
    agents_by_prompt: dict[str, list[dict]] = {}
    starts_by_prompt: dict[str, list[dict]] = {}
    for s in spans:
        if s.get('name') == 'tool.Agent':
            pid = _enclosing_prompt_id(s, by_id)
            if pid:
                agents_by_prompt.setdefault(pid, []).append(s)
        elif s.get('name') == 'subagent.start':
            pid = _enclosing_prompt_id(s, by_id)
            if pid:
                starts_by_prompt.setdefault(pid, []).append(s)
    for pid, starts in starts_by_prompt.items():
        agents = agents_by_prompt.get(pid) or []
        if len(starts) != 1 or len(agents) != 1:
            continue
        impact = agents[0].get('input_tokens')
        if impact:
            starts[0].setdefault('attributes', {})['main_session_impact_tokens'] = int(impact)


def _attach_prompt_expansions(trace_id: str, spans: list[dict]) -> None:
    """Attach expanded_text to prompt spans from the transcript scan.

    Slash-command prompts (e.g. /review) have a bare command in the text
    attribute but carry a full expansion in the isMeta child entry of the
    transcript. This function reads the transcript, extracts the expansions,
    and attaches them as `expanded_text` attributes on the matching prompts
    so the frontend can show both the concise label and the full expansion.

    Pure serve-time derivation; mutates the shared `attributes` dicts in
    place. No DB schema change needed."""
    from lib.orm.engine import get_connection as _get_connection
    from lib.settings import settings
    from pathlib import Path

    # Get the transcript path for this session
    if not hasattr(settings, 'transcript_dir'):
        return
    transcript_path = Path(settings.transcript_dir) / f'{trace_id}.jsonl'
    if not transcript_path.exists():
        return

    # Read the transcript and extract prompt_expansions
    try:
        from lib.trace.transcript_usage import read_usage
        usage = read_usage(str(transcript_path), max_text_bytes=None)
        if not usage or not usage.prompt_expansions:
            return
    except Exception:
        # If transcript parsing fails, just skip attachment
        return

    # Attach expanded_text to matching prompt spans
    by_id = {s['span_id']: s for s in spans}
    for prompt_uuid, expansion_text in usage.prompt_expansions.items():
        # The span_id format for transcript-derived prompts is `prompt-<uuid[:13]>`
        span_id_key = f'prompt-{prompt_uuid[:13]}'
        span = by_id.get(span_id_key)
        if span and span.get('name') == 'prompt':
            attrs = span.setdefault('attributes', {})
            if isinstance(attrs, dict):
                attrs['expanded_text'] = expansion_text


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
               context_used_tokens, request_id, effort_level
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
        turns, span_rows, server_side_uuids = _fetch_turn_usage_rows(conn, trace_id)
    finally:
        conn.close()

    if not turns:
        return turns

    turn_bounds = [_to_utc(t.get('timestamp')) for t in turns]
    buckets = _assign_spans_to_turns(span_rows, turn_bounds)

    for turn, bucket in zip(turns, buckets):
        turn['span_refs'] = bucket
        turn['span_count'] = len(bucket)
        turn['tool_summary'] = _tool_summary(bucket)
        turn['is_server_side'] = turn.get('turn_uuid') in server_side_uuids

    return turns
