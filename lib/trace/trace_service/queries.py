"""Read-side queries for the trace dashboard.

Pagination + projection helpers behind the /api/skill-reads,
/api/mcp-calls, /api/sessions/<trace_id>/spans, and per-turn endpoints.
SQL is raw because SQLite-specific `json_extract` + CTEs don't translate
cleanly to SQLAlchemy. `get_connection` is imported lazily so tests can
monkey-patch `lib.orm.engine.get_connection`.

The per-tool token rollup lives in `tool_rollup`, the per-turn usage rows
in `turn_usage`, and the serve-time prompt expansions in
`prompt_expansions`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from lib.trace.trace_service.prompt_expansions import (
    _attach_prompt_expansions,
    _reset_prompt_expansion_cache,
)
from lib.trace.trace_service.tool_rollup import fetch_tool_token_rollup  # noqa: F401
from lib.trace.trace_service.turn_usage import _to_utc, fetch_turn_usage  # noqa: F401
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
#
# `trace_id IS NOT NULL` is load-bearing, not defensive noise. `sessions`
# is a legacy (non-STRICT) rowid table, so its `TEXT PRIMARY KEY` does NOT
# imply NOT NULL — SQLite accepts a NULL there. A single NULL in the
# sub-select makes `NOT IN` evaluate to NULL for every row, silently
# blanking the skill-reads, mcp-calls and plans feeds with no error. The
# old span-scan could not hit this because `session_spans.trace_id` is
# declared NOT NULL.
#
# Known, accepted divergence from the old span scan: a PENDING placeholder
# span is written to `session_spans` but excluded from the sessions counter
# upsert, so a trace whose *only* is_test span is pending marks the old
# query and not this one. It closes as soon as any non-pending span lands.
_TEST_EXCLUSION = (
    "{col} NOT IN ("
    "SELECT trace_id FROM sessions WHERE is_test = 1 AND trace_id IS NOT NULL"
    ")"
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

def fetch_session_projection(
    trace_id: str, *, drop_delegated: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Read-only: fetch + graft-orphans + widen-envelopes.

    Returns (widened_spans, tree). Does not mutate the DB — callers
    that want the cleanup persisted use `materialize_session`.

    `drop_delegated` withholds the spans a subagent trace serves
    (`projection.drop_delegated_spans`). Off by default and named at each call
    site rather than inferred here: a reader that ANALYSES the session — the
    grader's evidence index — needs every row the agents produced, and a
    session whose work was all delegated would otherwise be graded as an empty
    artifact.
    """
    from lib.orm.engine import get_connection
    from lib.trace.alias import strip_origin, trace_group
    from lib.trace.merge import merge_spans
    from lib.trace.projection import (
        _attach_agent_rollups, _build_span_tree, _fetch_spans_for_group,
        _widen_envelopes, drop_delegated_spans,
    )
    from lib.trace.subagent_trace import stored_group

    conn = get_connection()
    try:
        # A regin-launched run is traced twice; both ids resolve to the same
        # group, and an ordinary session resolves to just itself.
        group = trace_group(conn, trace_id)
        trace_id = group[0]
        # Where the rows this id serves are STORED — the parent's group for a
        # subagent trace, whose session row, workflow labels and transcript all
        # belong to the run that launched it.
        stored = stored_group(conn, trace_id)
        raw = _fetch_spans_for_group(conn, group)
        grafted = strip_origin(merge_spans(
            raw, prompt_id_ceiling=_prompt_ceiling(conn, group),
            session_activity=_session_activity(conn, stored),
        ))
        widened = _widen_envelopes(grafted)
        _attach_compaction_reclaim(conn, stored[0], widened)
        _attach_subagent_impact(widened)
        _attach_prompt_expansions(stored[0], widened, conn)
        attach_workflow_agent_attrs(stored[0], widened, conn)
        # Recount on the MERGED rows. The fetch stamped the markers before
        # `merge_spans` ran, and on a session both writers recorded that
        # population holds each span twice under two different span_ids —
        # only the cross-source pairing here can tell they are one call. One
        # measured agent's card claimed 45 spans and 32 tools for the 29 and
        # 16 its own trace served.
        _attach_agent_rollups(widened)
        # After the derivations above, every one of which reads the agent's
        # spans to answer for the marker that survives them.
        served = drop_delegated_spans(widened) if drop_delegated else widened
        tree = _build_span_tree(served)
        return served, tree
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


# A subagent's turns are anchored by its `prompt-sa-…` launch prompt where it
# has one, and by its `subagent.start` marker where it does not — the same
# precedence `projection._fallback_orphan_anchor` gives the child's tree. Three
# quarters of stored agents have no launch prompt, so paging a child on
# `prompt` alone finds no anchor at all and serves it unwindowed.
_SUBAGENT_ANCHOR_NAME = 'subagent.start'


def _anchor_names(trace_id, *, after: bool) -> tuple:
    """The span names that page `trace_id`."""
    from lib.trace.subagent_trace import is_subagent_trace_id
    ids = [trace_id] if isinstance(trace_id, str) else list(trace_id)
    base = _AFTER_ID_ANCHOR_NAMES if after else _TURN_ANCHOR_NAMES
    if ids and is_subagent_trace_id(ids[0]):
        return (*base, _SUBAGENT_ANCHOR_NAME)
    return base


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
    from lib.trace.subagent_trace import span_scope
    scope, sparams = span_scope(conn, trace_id)
    names = _anchor_names(trace_id, after=after_id is not None)
    placeholders = ','.join('?' for _ in names)
    if after_id is not None:
        return conn.execute(
            f"""
            SELECT id, start_time FROM session_spans
            WHERE {scope}
              AND name IN ({placeholders})
              AND (id > ? OR status_code = 'PENDING')
            ORDER BY start_time ASC, id ASC
            LIMIT 500
            """,
            (*sparams, *names, after_id),
        ).fetchall()
    if before_id is not None:
        return conn.execute(
            f"""
            SELECT id, start_time FROM session_spans
            WHERE {scope}
              AND name IN ({placeholders})
              AND id < ?
            ORDER BY start_time DESC, id DESC
            LIMIT ?
            """,
            (*sparams, *names, before_id, limit),
        ).fetchall()
    return conn.execute(
        f"""
        SELECT id, start_time FROM session_spans
        WHERE {scope}
          AND name IN ({placeholders})
        ORDER BY start_time DESC, id DESC
        LIMIT ?
        """,
        (*sparams, *names, limit),
    ).fetchall()


def _window_end_for(conn, trace_id, before_id):
    """Step 2: upper bound for a before_id page.

    Cap the upper bound at the cursor's start_time so the page doesn't
    bleed in roots from already-loaded later turns (the cursor itself and
    beyond are owned by the caller's existing tree). For initial and
    after_id pages, no upper bound — we want everything from the window
    start onward.
    """
    from lib.trace.subagent_trace import span_scope
    if before_id is None:
        return None
    scope, sparams = span_scope(conn, trace_id)
    cursor_row = conn.execute(
        "SELECT start_time FROM session_spans "
        f"WHERE {scope} AND id = ?",
        (*sparams, before_id),
    ).fetchone()
    return cursor_row['start_time'] if cursor_row else None


def _compute_has_more_older(conn, trace_id, after_id, window_start):
    """Step 3: do we have any older anchors? Drives the
    `↑ More history above` indicator on the frontend.

    Reload (after_id) doesn't care about older history — that's already
    rendered. Caller passes in its existing has_more_older.
    """
    from lib.trace.subagent_trace import span_scope
    if after_id is not None:
        return False
    scope, sparams = span_scope(conn, trace_id)
    names = _anchor_names(trace_id, after=False)
    placeholders = ','.join('?' for _ in names)
    older = conn.execute(
        f"""
        SELECT 1 FROM session_spans
        WHERE {scope}
          AND name IN ({placeholders})
          AND start_time < ?
        LIMIT 1
        """,
        (*sparams, *names, window_start),
    ).fetchone()
    return older is not None


def _fetch_window_rows(conn, trace_id, window_start, window_end):
    """Step 4: fetch every span in the window, with attributes decoded.

    Delegates so this reader and the whole-session one share ONE column list:
    they had already drifted apart (`tool_use_id` was absent here), and a
    column the SELECT omits is invisible to the projection downstream.
    """
    from lib.trace.projection import _fetch_spans_for_group
    return _fetch_spans_for_group(conn, trace_id, window_start=window_start,
                                  window_end=window_end)


# Enough of the newest prompt rows to hold the newest anchor, its own
# placeholder, and the anchor before it — what `prompt_births` needs to date
# the newest turn — with room for stray placeholders between them.
_CEILING_PROMPT_ROWS = 16


def _json_attrs(raw) -> dict:
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _prompt_ceiling(conn, trace_id):
    """The GLOBAL max MAIN-agent prompt id, threaded into merge_spans so a stray
    prompt placeholder still drops when it happens to be the newest anchor
    *within an older window* (window-local max alone would keep it — see
    merge._drop_stale_blockers). Subagent launch prompts (`prompt-sa-`,
    agent_id set) are excluded: they are not main turn anchors, so a subagent
    prompt with a higher id must never raise the cutoff and drop the user's
    live main placeholder.

    In a subagent trace that exclusion is inverted: every prompt the child
    serves is agent-scoped, so keeping it would leave the ceiling NULL and
    forfeit the guard entirely."""
    from lib.trace.alias import sql_in
    from lib.trace.merge_pending import prompt_births
    from lib.trace.pending_spans import AGENT_ID_SQL
    from lib.trace.subagent_trace import agent_span_scope
    agent = agent_span_scope(conn, trace_id)
    if agent is not None:
        scope, params, main_only = agent.clause, list(agent.params), ''
    else:
        placeholders, params = sql_in(trace_id)
        scope = f"trace_id IN {placeholders}"
        main_only = f" AND {AGENT_ID_SQL} IS NULL"
    rows = conn.execute(
        "SELECT id, trace_id, span_id, attributes FROM session_spans "
        f"WHERE {scope} AND name = 'prompt'{main_only} "
        "ORDER BY id DESC LIMIT ?",
        [*params, _CEILING_PROMPT_ROWS],
    ).fetchall()
    if not rows:
        return None
    prompts = [{'id': r[0], 'trace_id': r[1], 'span_id': r[2],
                'name': 'prompt', 'attributes': _json_attrs(r[3])}
               for r in rows]
    births = prompt_births(prompts)
    return max(births.get((p['trace_id'], p['span_id']), p['id'])
               for p in prompts)


def _session_activity(conn, trace_id):
    """{'status', 'last_seen'} for the session, threaded into merge_spans so
    the stuck-pending demotion can tell an active session (never demote) from
    an inactive/ended one (demote old blockers). None when the row is absent
    (a not-yet-summarised trace) — demotion then falls back to the
    same-agent-moved-on path alone.

    Takes the STORED group (`subagent_trace.stored_group`), because a subagent
    trace has no row of its own and is exactly as active as the run it is part
    of."""
    from lib.trace.alias import sql_in
    placeholders, params = sql_in(trace_id)
    # Over an alias group the two rows are summarised independently, so the
    # freshest one is the session's real activity — taking the canonical row
    # alone would call a live run idle whenever the child's row lags.
    row = conn.execute(
        f"SELECT status, last_seen FROM sessions WHERE trace_id IN {placeholders} "
        "ORDER BY last_seen DESC LIMIT 1",
        params,
    ).fetchone()
    if row is None:
        return None
    from lib.agent_acp.registry import open_tool_use_ids
    from lib.trace.pending_spans import never_ran_pending_agents
    return {'status': row['status'], 'last_seen': row['last_seen'],
            'never_ran': never_ran_pending_agents(conn, params),
            'open_tool_ids': open_tool_use_ids(params)}


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
    drop_delegated: bool = False,
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

    `drop_delegated` is the display opt-in — see `fetch_session_projection`.

    Returns `(widened_spans, tree, has_more_older)`.
    """
    if before_id is not None and after_id is not None:
        raise ValueError("before_id and after_id are mutually exclusive")
    if limit < 1:
        raise ValueError("limit must be >= 1")

    from lib.orm.engine import get_connection
    from lib.trace.merge import merge_spans
    from lib.trace.projection import (
        _attach_agent_rollups, _build_span_tree, _widen_envelopes,
        drop_delegated_spans,
    )
    from lib.trace.wakeup_links import annotate_wakeup_resumes

    conn = get_connection()
    try:
        # Both ids of a regin-launched run page as one session; an ordinary
        # session resolves to just itself. Every helper below takes the group,
        # so an anchor on the SDK side can't be paged past.
        from lib.trace.alias import strip_origin, trace_group
        from lib.trace.subagent_trace import stored_group
        group = trace_group(conn, trace_id)
        # `group` addresses the store (every span-level query below);
        # `trace_id` stays the canonical id for the session-level joins, which
        # key on one session and would break on a list. For a subagent trace
        # those joins follow the PARENT, which owns the session row and the
        # workflow spans the child's markers are labelled from.
        trace_id = group[0]
        stored = stored_group(conn, trace_id)

        # Step 1: page the turn anchors.
        anchors = _page_anchors(
            conn, group,
            limit=limit, before_id=before_id, after_id=after_id,
        )
        if not anchors and (before_id is not None or after_id is not None):
            return [], [], False, []

        if anchors:
            # Step 2: window = [earliest_anchor_in_page, upper_bound).
            window_start = min(a['start_time'] for a in anchors)
            window_end = _window_end_for(conn, group, before_id)

            # Step 3: do we have any older anchors?
            has_more_older = _compute_has_more_older(
                conn, group, after_id, window_start,
            )
        else:
            # Initial mount of a session with no turn anchor yet (opened
            # before its first prompt): serve every span unwindowed, or the
            # pre-prompt roots (session.start, environment.*, …) are
            # invisible until a prompt lands.
            window_start = window_end = None
            has_more_older = False

        # Step 4: fetch every span in the window.
        raw = _fetch_window_rows(conn, group, window_start, window_end)

        # Step 5: standard projection on the windowed subset. merge_spans
        # dedups coexisting placeholder/pending rows (append-only store) then
        # runs the deterministic reparent ladder.
        grafted = strip_origin(merge_spans(
            raw, prompt_id_ceiling=_prompt_ceiling(conn, group),
            session_activity=_session_activity(conn, stored),
        ))
        # Before the delegated rows leave: the client prunes this list from
        # its cards, and a row served by a child trace has not been superseded
        # by anything the client holds.
        retired_ids = _compute_retired_ids(raw, grafted)
        widened = _widen_envelopes(grafted)
        _attach_compaction_reclaim(conn, stored[0], widened)
        _attach_subagent_impact(widened)
        attach_workflow_agent_attrs(stored[0], widened, conn)
        annotate_wakeup_resumes(widened)
        # Recount on the MERGED rows. The fetch stamped the markers before
        # `merge_spans` ran, and on a session both writers recorded that
        # population holds each span twice under two different span_ids —
        # only the cross-source pairing here can tell they are one call. One
        # measured agent's card claimed 45 spans and 32 tools for the 29 and
        # 16 its own trace served.
        _attach_agent_rollups(widened)
        served = drop_delegated_spans(widened) if drop_delegated else widened
        tree = _build_span_tree(served)
        return served, tree, has_more_older, retired_ids
    finally:
        conn.close()


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
    before = _first_context(reversed(turns), lambda ts: ts <= pre_at)
    after = _first_context(turns, lambda ts: ts >= post_at)
    if before is None or after is None:
        return None
    delta = before - after
    return delta if delta > 0 else None


def _first_context(turns, accept) -> int | None:
    """First non-zero context size among `turns` whose timestamp is set and
    satisfies `accept`."""
    return next((ctx for ts, ctx in turns if ts and ctx and accept(ts)), None)


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
    pre_times = _compact_pre_times(conn, trace_id)
    turns = _turn_context_sizes(conn, trace_id)
    for post in posts:
        reclaimed = _reclaimed_for_post(post, pre_times, turns)
        if reclaimed is not None:
            post.setdefault('attributes', {})['reclaimed_tokens'] = reclaimed


def _compact_pre_times(conn, trace_id: str) -> list[datetime]:
    pres = conn.execute(
        "SELECT start_time FROM session_spans "
        "WHERE trace_id = ? AND name = 'compact.pre' "
        "ORDER BY start_time ASC",
        (trace_id,),
    ).fetchall()
    return sorted(
        t for t in (_to_utc(r['start_time']) for r in pres) if t
    )


def _turn_context_sizes(conn, trace_id: str) -> list[tuple]:
    turn_rows = conn.execute(
        "SELECT timestamp, context_used_tokens FROM turn_usage "
        "WHERE trace_id = ? ORDER BY timestamp ASC",
        (trace_id,),
    ).fetchall()
    return [(_to_utc(r['timestamp']), r['context_used_tokens']) for r in turn_rows]


def _reclaimed_for_post(post: dict, pre_times: list[datetime], turns: list[tuple]) -> int | None:
    post_at = _to_utc(post.get('start_time'))
    if post_at is None:
        return None
    # Pair with the latest compact.pre at/before this post.
    pre_at = next((t for t in reversed(pre_times) if t <= post_at), None)
    if pre_at is None:
        return None
    return _reclaimed_for_pair(pre_at, post_at, turns)


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


def _group_by_enclosing_prompt(spans: list[dict], name: str) -> dict[str, list[dict]]:
    """Spans called `name`, keyed by their enclosing prompt span_id (spans
    with no enclosing prompt are dropped)."""
    by_id = {s['span_id']: s for s in spans}
    grouped: dict[str, list[dict]] = {}
    for s in spans:
        if s.get('name') != name:
            continue
        pid = _enclosing_prompt_id(s, by_id)
        if pid:
            grouped.setdefault(pid, []).append(s)
    return grouped


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
    agents_by_prompt = _group_by_enclosing_prompt(spans, 'tool.Agent')
    starts_by_prompt = _group_by_enclosing_prompt(spans, 'subagent.start')
    for pid, starts in starts_by_prompt.items():
        agents = agents_by_prompt.get(pid) or []
        if len(starts) != 1 or len(agents) != 1:
            continue
        impact = agents[0].get('input_tokens')
        if impact:
            starts[0].setdefault('attributes', {})['main_session_impact_tokens'] = int(impact)


