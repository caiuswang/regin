"""Write-side ingest + materialization for trace data.

Persists per-turn usage rows, attaches per-tool token estimates to spans,
upserts the session statusline snapshot, and writes the span ingest path
(the ~60-line ON CONFLICT DO UPDATE in `_SESSIONS_UPSERT_SQL`).
`materialize_session` re-runs the projection (graft + widen) and writes
the result back to the DB. `get_connection` is imported lazily so tests
can monkey-patch `lib.orm.engine.get_connection`.
"""

from __future__ import annotations

import json

from lib.activity_log import get_activity_logger as _get_activity_logger
from lib.trace.pending_spans import is_pending_span_id


def _trace_log():
    return _get_activity_logger("trace_ingest")


# ── Session projection materialize ──────────────────────────

def materialize_session(trace_id: str) -> dict:
    """Persist the orphan-graft + envelope-widen projection to the DB.

    Wrapped in BEGIN IMMEDIATE so fetch + multiple updates form one
    atomic unit. Raises on any DB error — caller turns it into a 500.

    Also refreshes the `sessions.active_work_ms` aggregate so the list
    view stays in sync with the materialised projection.
    """
    from lib.orm.engine import get_connection
    from lib.trace.projection import (
        _compute_active_work_ms, _fetch_spans, _graft_orphans,
        _persist_projection, _widen_envelopes,
    )

    conn = get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        raw = _fetch_spans(conn, trace_id)
        grafted = _graft_orphans(raw)
        widened = _widen_envelopes(grafted)
        updated = _persist_projection(conn, trace_id, raw, widened)
        active_ms = _compute_active_work_ms(widened)
        conn.execute(
            "UPDATE sessions SET active_work_ms = ? WHERE trace_id = ?",
            (active_ms, trace_id),
        )
        conn.commit()
        updated['active_work_ms'] = active_ms
        _trace_log().write(
            "session_projection_materialized",
            trace_id=trace_id, active_work_ms=active_ms,
            span_count=len(raw),
        )
        return updated
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _refresh_active_work_ms(conn, trace_ids) -> None:
    """Recompute and persist `sessions.active_work_ms` for each trace.

    Re-runs graft + widen on all spans in the trace so the value matches
    what the detail view computes from the projected tree. Skipped for
    traces that have no row yet (the upsert running concurrently in the
    same transaction will create it; we get there first only when the
    very first span lands and the row is committed by the upsert before
    this fires).
    """
    from lib.trace.projection import (
        _compute_active_work_ms, _fetch_spans,
        _graft_orphans, _widen_envelopes,
    )

    for tid in trace_ids:
        if not tid:
            continue
        raw = _fetch_spans(conn, tid)
        if not raw:
            continue
        widened = _widen_envelopes(_graft_orphans(raw))
        conn.execute(
            "UPDATE sessions SET active_work_ms = ? WHERE trace_id = ?",
            (_compute_active_work_ms(widened), tid),
        )
# ── Session-span ingest ──────────────────────────────────────

def _validate_turn_row(r) -> tuple[str, str, str] | None:
    """Return (trace_id, turn_uuid, timestamp) for a well-formed turn-usage
    row, or None when the row is malformed and should be skipped."""
    if not isinstance(r, dict):
        return None
    trace_id = r.get('trace_id')
    turn_uuid = r.get('turn_uuid')
    timestamp = r.get('timestamp')
    if not (isinstance(trace_id, str) and trace_id
            and isinstance(turn_uuid, str) and turn_uuid
            and isinstance(timestamp, str) and timestamp):
        return None
    return trace_id, turn_uuid, timestamp


def _insert_one_turn_row(conn, r, trace_id, turn_uuid, timestamp) -> None:
    """Upsert a single validated turn-usage row by (trace_id, turn_uuid)."""
    from lib.tokens.pricing import TokenBreakdown, cost as pricing_cost

    # Compute per-turn USD cost from the model + token mix the
    # hook just reported. Falls back to NULL when the model
    # isn't in the catalogue — a non-Anthropic model, an old
    # transcript that never carried a model name, or pricing
    # data being unreachable. We never block ingest on this.
    input_tokens = int(r.get('input_tokens') or 0)
    output_tokens = int(r.get('output_tokens') or 0)
    cache_read_tokens = int(r.get('cache_read_tokens') or 0)
    cache_creation_tokens = int(r.get('cache_creation_tokens') or 0)
    context_used_tokens = int(r.get('context_used_tokens') or 0)
    model = r.get('model')
    row_cost: float | None = None
    try:
        row_cost = pricing_cost(model, TokenBreakdown(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        ), context_tokens=context_used_tokens)
    except Exception:
        row_cost = None

    conn.execute(
        """
        INSERT INTO turn_usage (
            trace_id, turn_uuid, turn_index, timestamp, model,
            input_tokens, output_tokens, cache_read_tokens,
            cache_creation_tokens, context_used_tokens, request_id,
            cost_usd, effort_level
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trace_id, turn_uuid) DO UPDATE SET
            turn_index = excluded.turn_index,
            timestamp  = excluded.timestamp,
            model      = COALESCE(excluded.model, turn_usage.model),
            input_tokens          = excluded.input_tokens,
            output_tokens         = excluded.output_tokens,
            cache_read_tokens     = excluded.cache_read_tokens,
            cache_creation_tokens = excluded.cache_creation_tokens,
            context_used_tokens   = excluded.context_used_tokens,
            request_id            = COALESCE(excluded.request_id,
                                            turn_usage.request_id),
            cost_usd              = COALESCE(excluded.cost_usd,
                                            turn_usage.cost_usd),
            effort_level          = COALESCE(excluded.effort_level,
                                            turn_usage.effort_level)
        """,
        (
            trace_id, turn_uuid,
            int(r.get('turn_index') or 0),
            timestamp,
            model,
            input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens,
            context_used_tokens,
            r.get('request_id'),
            row_cost,
            r.get('effort_level'),
        ),
    )


def _insert_turn_usage_rows(conn, rows) -> tuple[int, int, set[str]]:
    """Validate + upsert each turn-usage row.

    Returns (inserted_count, skipped_count, touched_trace_ids)."""
    touched_traces: set[str] = set()
    inserted = 0
    skipped = 0
    for r in rows:
        validated = _validate_turn_row(r)
        if validated is None:
            skipped += 1
            continue
        trace_id, turn_uuid, timestamp = validated
        _insert_one_turn_row(conn, r, trace_id, turn_uuid, timestamp)
        inserted += 1
        touched_traces.add(trace_id)
    return inserted, skipped, touched_traces


def _live_context_peak(conn, trace_id, main_rows, peak_main):
    """Main-flow context high-water mark *since the most recent `/compact`*.

    `main_rows` is `[(timestamp, context_used_tokens), …]` for the
    session's main-conversation turns. A compaction (manual or auto)
    resets Claude Code's live context window, so we restrict the peak to
    turns at/after the latest `compact.post` boundary — the all-time peak
    otherwise stays pinned at the pre-compaction high.

    Falls back to the all-time `peak_main` when the session never
    compacted, or when the boundary has landed but no following turn has
    been ingested yet (so the headline never regresses below a sensible
    default). Timestamps are normalised to UTC before comparison: turn
    timestamps are UTC-with-`Z`, the boundary span's `start_time` is
    naive host-local, so a raw string compare would mis-bracket them.
    """
    from lib.trace.trace_service.queries import _to_utc

    cut_row = conn.execute(
        "SELECT MAX(start_time) FROM session_spans "
        "WHERE trace_id = ? AND name = 'compact.post'",
        (trace_id,),
    ).fetchone()
    cut = _to_utc(cut_row[0]) if cut_row and cut_row[0] else None
    if cut is None:
        return peak_main
    seg = [
        ctx for ts, ctx in main_rows
        if isinstance(ctx, int) and (tsu := _to_utc(ts)) and tsu >= cut
    ]
    return max(seg) if seg else peak_main


def _refresh_session_aggregates(conn, trace_ids) -> None:
    """Re-derive session-row aggregates from turn_usage so the
    header/list views stay authoritative."""
    from lib.tokens.model_windows import infer_window

    for tid in trace_ids:
        row = conn.execute("""
            SELECT SUM(input_tokens),  SUM(output_tokens),
                   SUM(cache_read_tokens), SUM(cache_creation_tokens),
                   MAX(context_used_tokens),
                   COUNT(*),
                   SUM(cost_usd)
            FROM turn_usage WHERE trace_id = ?
        """, (tid,)).fetchone()
        if not row or row[5] == 0:
            continue
        (in_tot, out_tot, cread, ccreate, peak, _cnt, cost_tot) = row
        # "Main" turns exclude those whose API call rolled in a
        # server-side sub-call. Anthropic charges the advisor's
        # internal iterations to the parent turn's `usage`, so those
        # turns overstate main-conversation context size. The
        # corresponding span carries attributes.server_side=true.
        # The turn_uuid column on session_spans is populated lazily
        # by ingest_tool_attribution, so we COALESCE with the value
        # the hook stamped into attributes.turn_uuid — guaranteed
        # present from the first ingest of any server_side span.
        # We pull the per-turn rows (not a bare MAX) to derive two
        # numbers from them: the all-time main peak, and the live peak.
        main_rows = conn.execute("""
            SELECT tu.timestamp, tu.context_used_tokens
              FROM turn_usage tu
             WHERE tu.trace_id = ?
               AND NOT EXISTS (
                   SELECT 1 FROM session_spans ss
                    WHERE ss.trace_id = tu.trace_id
                      AND COALESCE(ss.turn_uuid,
                                   json_extract(ss.attributes, '$.turn_uuid'))
                          = tu.turn_uuid
                      AND json_extract(ss.attributes, '$.server_side') = 1
               )
        """, (tid,)).fetchall()
        main_ctx = [r[1] for r in main_rows if isinstance(r[1], int)]
        peak_main = max(main_ctx) if main_ctx else None
        # Live context peak: the high-water mark *since the most recent
        # `/compact`*. A compaction (manual or auto) resets Claude
        # Code's live context window, but the all-time peaks above stay
        # pinned at the pre-compaction high and misrepresent how full
        # the window is now. `live_context_tokens` drives the headline
        # ctx% so it drops when the session compacts; the all-time peaks
        # remain for window inference and the pre-compaction hint.
        live = _live_context_peak(conn, tid, main_rows, peak_main)
        # Read sessions.model (may be None for a freshly-ingesting
        # session) to compute the window; infer_window handles the
        # None case gracefully. Window inference still uses the
        # all-inclusive peak — the 1M-variant promotion heuristic
        # needs to see advisor-inflated turns to fire on sessions
        # whose main flow never crosses 200K but ran on `[1m]`.
        sess = conn.execute(
            "SELECT model FROM sessions WHERE trace_id = ?", (tid,)
        ).fetchone()
        model = sess[0] if sess else None
        window = infer_window(model, int(peak or 0))
        conn.execute("""
            UPDATE sessions SET
                input_tokens = ?, output_tokens = ?,
                cache_read_tokens = ?, cache_creation_tokens = ?,
                peak_context_tokens = ?,
                peak_main_context_tokens = ?,
                live_context_tokens = ?,
                context_window_tokens = ?,
                cost_usd = ?
            WHERE trace_id = ?
        """, (in_tot, out_tot, cread, ccreate, peak, peak_main, live, window,
              cost_tot, tid))


def ingest_turn_usage(rows: list[dict]) -> tuple[int, int]:
    """Insert per-turn usage rows and refresh the owning session row.

    Upsert is (trace_id, turn_uuid) → keep the row, update its counters.
    After inserting, recompute the session-level aggregates
    (peak_context_tokens, input_tokens sums, model) from the
    `turn_usage` table so there's no risk of drift.

    Returns (inserted_or_updated_count, malformed_skipped_count).
    """
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        inserted, skipped, touched_traces = _insert_turn_usage_rows(conn, rows)
        _refresh_session_aggregates(conn, touched_traces)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
    _trace_log().write(
        "turn_usage_ingested",
        inserted=inserted, skipped=skipped,
        trace_count=len(touched_traces),
    )
    return inserted, skipped


def _turn_context_tokens(conn, trace_id: str, turn_uuid: str | None) -> int | None:
    """Context size of a turn, for context-tiered pricing. None when the
    turn_usage row hasn't been ingested yet — cost then falls back to the
    base tier (and a later `trace backfill-costs --recompute` can correct
    it once turn usage lands)."""
    if not turn_uuid:
        return None
    row = conn.execute(
        "SELECT context_used_tokens FROM turn_usage "
        "WHERE trace_id = ? AND turn_uuid = ?",
        (trace_id, turn_uuid),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _as_int_or_none(v: object) -> int | None:
    return int(v) if isinstance(v, (int, float)) else None


def _attribute_one_call(
    conn,
    tc: object,
    *,
    model: str | None,
    turn_ctx: int | None,
    turn_uuid: str | None,
    parent_span_id: str | None,
    trace_id: str,
) -> bool:
    """UPDATE the session_spans row(s) matching one tool_call's
    `tool_use_id` with its token estimates, cost, and (for `tool.*` rows
    only) the backfilled parent_id. Returns True iff a row was updated.

    The `parent_id` CASE is name-scoped so a `permission.request` /
    `task.notification` row sharing the same tool_use_id keeps its own
    parentage — only the live `tool.<name>` span (posted parent-less at
    PostToolUse time) gets the issuing turn's `resp-`/`think-` parent.
    COALESCE means an already-parented tool span (server/deny synth) is
    left untouched."""
    from lib.tokens.pricing import TokenBreakdown, cost
    if not isinstance(tc, dict):
        return False
    tu_id = tc.get('tool_use_id')
    if not isinstance(tu_id, str) or not tu_id:
        return False
    out_tok = _as_int_or_none(tc.get('output_tokens'))
    in_tok = _as_int_or_none(tc.get('input_tokens'))
    img_tok = _as_int_or_none(tc.get('image_tokens'))
    # Cost bundles output (this turn's API output bill) + input (the
    # tool_result feeding the next turn's input bill) into one USD.
    usd = cost(model, TokenBreakdown(
        input_tokens=in_tok or 0,
        output_tokens=out_tok or 0,
    ), context_tokens=turn_ctx) if model else None
    # Prefer the authoritative image-token count when the PostToolUse
    # span stamped one from Claude Code's reported display dimensions
    # (`attributes.image_tokens_exact`); fall back to the base64-header
    # estimate otherwise. The Read span is created before this UPDATE
    # (post_tool_trace priority 110 < turn_trace 150), so the attribute
    # is present whenever it applies.
    cur = conn.execute(
        """
        UPDATE session_spans
           SET output_tokens = ?,
               input_tokens  = ?,
               image_tokens  = COALESCE(
                   CAST(json_extract(attributes, '$.image_tokens_exact')
                        AS INTEGER),
                   ?),
               cost_usd      = ?,
               tool_use_id   = COALESCE(tool_use_id, ?),
               turn_uuid     = COALESCE(turn_uuid, ?),
               parent_id     = CASE WHEN name LIKE 'tool.%'
                                    THEN COALESCE(parent_id, ?)
                                    ELSE parent_id END
         WHERE trace_id = ?
           AND (tool_use_id = ?
                OR json_extract(attributes, '$.tool_use_id') = ?)
        """,
        (out_tok, in_tok, img_tok, usd, tu_id, turn_uuid, parent_span_id,
         trace_id, tu_id, tu_id),
    )
    return cur.rowcount > 0


def ingest_tool_attribution(payload: dict) -> tuple[int, int]:
    """Attach per-tool token estimates to existing `tool.*` spans.

    Anthropic's API returns one `usage` per assistant turn — we can't
    get exact per-tool numbers from the wire. Instead, the caller
    tokenizes each transcript `tool_use` (output) and `tool_result`
    (input) locally via `lib.tokens.token_estimator` and posts the result
    here. We UPDATE the matching `session_spans` row by `tool_use_id`
    (looked up first in the column, then in `attributes.tool_use_id`
    for older spans), set the new token columns, and compute
    `cost_usd` from the session's recorded model rate.

    Body: `{trace_id, turn_uuid, parent_span_id?, tool_calls: [{tool_use_id,
            name?, output_tokens, input_tokens, image_tokens?}]}`.

    Returns (updated_count, skipped_count).
    """
    from lib.orm.engine import get_connection

    if not isinstance(payload, dict):
        return 0, 1
    trace_id = payload.get('trace_id')
    turn_uuid = payload.get('turn_uuid')
    tool_calls = payload.get('tool_calls')
    # Issuing turn's resp-/think- span (or None). Backfills the live
    # tool.* span's parent_id, which is posted parent-less at PostToolUse
    # time. Name-scoped in SQL so it never disturbs the parentage of a
    # permission.request / task.notification row that happens to share a
    # tool_use_id.
    parent_span_id = payload.get('parent_span_id')
    if not isinstance(parent_span_id, str) or not parent_span_id:
        parent_span_id = None
    if not isinstance(trace_id, str) or not trace_id:
        return 0, 1
    if not isinstance(tool_calls, list):
        return 0, 1
    if not isinstance(turn_uuid, str):
        turn_uuid = None

    conn = get_connection()
    try:
        sess_row = conn.execute(
            "SELECT model FROM sessions WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        model = sess_row[0] if sess_row else None
        turn_ctx = _turn_context_tokens(conn, trace_id, turn_uuid)

        updated = 0
        skipped = 0
        for tc in tool_calls:
            if _attribute_one_call(
                conn, tc, model=model, turn_ctx=turn_ctx, turn_uuid=turn_uuid,
                parent_span_id=parent_span_id, trace_id=trace_id,
            ):
                updated += 1
            else:
                skipped += 1
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
    _trace_log().write(
        "tool_attribution_ingested",
        trace_id=trace_id, updated=updated, skipped=skipped,
    )
    return updated, skipped


def ingest_session_status(
    trace_id: str,
    model: str | None = None,
    context_used_tokens: int | None = None,
    context_window_tokens: int | None = None,
) -> None:
    """Persist an authoritative model + context snapshot for a session.

    Sourced from Claude Code's statusline JSON via
    `scripts/regin-statusline` — the only runtime surface that carries
    the model variant suffix (`[1m]`) and the real context-window total.

    Model precedence matches the session-spans upsert: a bare base id
    never overwrites a more-specific stored variant (see
    `_is_less_specific_model`). `peak_context_tokens` is
    `MAX(existing, incoming)` so a brief dip after `/compact` doesn't
    move the stored peak backwards.

    The row is upserted — the script can fire before SessionStart has
    landed a row, and we don't want the first statusline tick to be
    silently dropped. `started_at` / `last_seen` fall back to `now`
    only when creating a fresh row; existing rows keep their real
    span-derived timestamps.
    """
    if not isinstance(trace_id, str) or not trace_id:
        return
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT model, peak_context_tokens FROM sessions WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO sessions (
                        trace_id, started_at, last_seen,
                        model, peak_context_tokens, context_window_tokens
                   ) VALUES (?, datetime('now'), datetime('now'), ?, ?, ?)""",
                (trace_id, model, context_used_tokens, context_window_tokens),
            )
            conn.commit()
            _trace_log().write(
                "session_status_ingested",
                trace_id=trace_id, action="created", model=model,
                context_used_tokens=context_used_tokens,
                context_window_tokens=context_window_tokens,
            )
            return

        current_model = row['model']
        # Keep the richer id when the incoming one is a bare base.
        if isinstance(model, str) and model:
            if _is_less_specific_model(model, current_model):
                model = current_model
        else:
            model = current_model

        current_peak = row['peak_context_tokens']
        peak = context_used_tokens
        if isinstance(current_peak, int):
            if not isinstance(peak, int) or peak < current_peak:
                peak = current_peak

        updates = ['model = ?', 'peak_context_tokens = ?']
        params: list = [model, peak]
        if context_window_tokens is not None:
            updates.append('context_window_tokens = ?')
            params.append(context_window_tokens)
        params.append(trace_id)
        conn.execute(
            f"UPDATE sessions SET {', '.join(updates)} WHERE trace_id = ?",
            params,
        )
        conn.commit()
        _trace_log().write(
            "session_status_ingested",
            trace_id=trace_id, action="updated", model=model,
            context_used_tokens=peak,
            context_window_tokens=context_window_tokens,
        )
    finally:
        conn.close()


def _is_less_specific_model(incoming: str, current: str | None) -> bool:
    """True when `incoming` is the bare base of a more-specific `current`,
    or when `incoming` is the placeholder `<synthetic>` that Claude Code
    writes for session init / /compact entries.

    Claude Code's SessionStart hook payload carries the variant suffix
    (e.g. ``claude-opus-4-7[1m]``) but the transcript JSONL always writes
    `message.model` as the bare base (``claude-opus-4-7``). The
    transcript-derived `turn` / `turn.usage` spans therefore pose a
    downgrade risk — this guard keeps the richer value.
    """
    # <synthetic> is never a real model and must never overwrite one.
    if incoming == '<synthetic>':
        return bool(current) and current != '<synthetic>'
    if not current:
        return False
    if incoming == current:
        return False
    return current.startswith(incoming + '[')


_TITLE_MAX_CHARS = 400


def _trim_title(text) -> str | None:
    """Collapse a session-title source to one short line.

    The first prompt of a session is often hundreds-to-thousands of chars
    of multi-paragraph instructions; storing it verbatim blows up the
    sessions-list row (one observed case: 39 792 chars). Take the first
    non-empty line and cap at `_TITLE_MAX_CHARS`, appending `…` when cut.
    Also applied defensively to `session.title` spans in case a future
    transcript carries a runaway custom title.
    """
    if not isinstance(text, str):
        return None
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), None)
    if not first_line:
        return None
    if len(first_line) > _TITLE_MAX_CHARS:
        return first_line[:_TITLE_MAX_CHARS].rstrip() + '…'
    return first_line


def _new_session_bucket() -> dict:
    return {
        'span_count': 0, 'skill_reads': 0, 'file_edits': 0,
        'rule_checks': 0, 'plan_enters': 0, 'prompts': 0,
        'tool_calls': 0, 'is_test': 0, 'test_name': None,
        'started_at': None, 'last_seen': None,
        'title': None, '_title_start': None,
        'live_title': None, 'live_title_source': None,
        'last_start_at': None,
        'ended_at': None, 'ended_reason': None,
        'agent_type': None, '_agent_type_start': None,
        'model': None, '_model_start': None,
        'cwd': None, '_cwd_start': None,
    }


# Span-name → counter-field. Mutually-exclusive single-counter mapping;
# the `tool.`/`pre_tool.` prefix counter is additive and handled
# separately so a `tool.Bash` span bumps `tool_calls` but no other
# counter. The file-edit tool spans (`tool.Edit`, `tool.Write`, …) map
# here for `file_edits` AND still bump `tool_calls` via the prefix rule.
#
# Reads span three disjoint signals — a skill content.md Read
# (`skill.read`), a slash-command expansion (`skill.invoke`), and an
# assistant `Skill` tool launch (`skill.launch`). `plan_enters` is no
# longer derived here: the Sessions list computes plans live from the
# `plan_sessions` table (see web/blueprints/trace/sessions.py).
_NAME_TO_COUNTER = {
    'skill.read': 'skill_reads',
    'skill.invoke': 'skill_reads',
    'skill.launch': 'skill_reads',
    'tool.Edit': 'file_edits',
    'tool.Write': 'file_edits',
    'tool.MultiEdit': 'file_edits',
    'tool.NotebookEdit': 'file_edits',
    'tool.apply_patch': 'file_edits',
    'rule.check': 'rule_checks',
    'prompt': 'prompts',
}


def _bump_named_counter(bucket: dict, name: str) -> None:
    counter = _NAME_TO_COUNTER.get(name)
    if counter:
        bucket[counter] += 1
    if name.startswith('tool.') or name.startswith('pre_tool.'):
        bucket['tool_calls'] += 1


def _apply_test_markers(bucket: dict, attrs: dict) -> None:
    if attrs.get('is_test'):
        bucket['is_test'] = 1
    tn = attrs.get('test_name')
    if tn and not bucket['test_name']:
        bucket['test_name'] = tn


def _update_time_bounds(bucket: dict, start, end) -> None:
    if start and (bucket['started_at'] is None or start < bucket['started_at']):
        bucket['started_at'] = start
    if end and (bucket['last_seen'] is None or end > bucket['last_seen']):
        bucket['last_seen'] = end


def _handle_prompt_title(bucket: dict, attrs: dict, start) -> None:
    """Earliest prompt by start_time becomes the 'first_prompt' title."""
    trimmed = _trim_title(attrs.get('text'))
    if not trimmed:
        return
    if bucket['_title_start'] is None or (start and start < bucket['_title_start']):
        bucket['title'] = trimmed
        bucket['_title_start'] = start


def _handle_session_title(bucket: dict, attrs: dict, start) -> None:
    """Title surfaced from the Claude Code transcript — either a
    `custom-title` line (user ran /rename → `user_rename` source) or an
    `ai-title` line (Claude's auto-generated → `claude_ai_title`). The
    hook overwrites a stable span_id, so only the latest value lands in
    the spans table. Within one batch, a `user_rename` always beats
    `claude_ai_title` even if the ai-title is processed later.
    """
    src = attrs.get('source')
    trimmed = _trim_title(attrs.get('text'))
    if not (trimmed and src in ('user_rename', 'claude_ai_title')):
        return
    if bucket['live_title_source'] != 'user_rename' or src == 'user_rename':
        bucket['live_title'] = trimmed
        bucket['live_title_source'] = src


def _apply_timed_attr(
    bucket: dict, attrs: dict, attr_key: str, value_field: str,
    tracker_field: str, start, *, latest: bool, store_stripped: bool,
) -> None:
    """Set a session-start string attribute under a time-precedence rule.

    `latest=True` keeps the value seen at the greatest `start` (latest
    wins); `latest=False` keeps the earliest. The decision is tracked
    against `tracker_field`, never `value_field`, so a fallback that
    pre-set the value without a tracker timestamp is still overridden.
    `store_stripped` writes the whitespace-trimmed value (agent_type,
    cwd); model stores the raw string.
    """
    raw = attrs.get(attr_key)
    if not (isinstance(raw, str) and raw.strip()):
        return
    tracker = bucket[tracker_field]
    if latest:
        wins = tracker is None or (start and start > tracker)
    else:
        wins = tracker is None or (start and start < tracker)
    if not wins:
        return
    bucket[value_field] = raw.strip() if store_stripped else raw
    bucket[tracker_field] = start


def _handle_session_start(bucket: dict, attrs: dict, start) -> None:
    """session.start carries `last_start_at` (latest wins), `agent_type`
    (earliest non-empty wins), and `model` (latest wins)."""
    if start and (bucket['last_start_at'] is None or start > bucket['last_start_at']):
        bucket['last_start_at'] = start
    _apply_timed_attr(bucket, attrs, 'agent_type', 'agent_type',
                      '_agent_type_start', start, latest=False, store_stripped=True)
    _apply_timed_attr(bucket, attrs, 'model', 'model',
                      '_model_start', start, latest=True, store_stripped=False)
    _apply_timed_attr(bucket, attrs, 'cwd', 'cwd',
                      '_cwd_start', start, latest=False, store_stripped=True)


def _handle_turn_model(bucket: dict, attrs: dict, start) -> None:
    """`turn` spans report the model from the transcript JSONL — same
    'latest wins' rule as session.start, but the bare-base form must
    not downgrade a variant-bracketed model already on the bucket."""
    m = attrs.get('model')
    if not (isinstance(m, str) and m.strip()):
        return
    if bucket['_model_start'] is not None and (not start or start <= bucket['_model_start']):
        return
    if _is_less_specific_model(m, bucket['model']):
        return
    bucket['model'] = m
    bucket['_model_start'] = start


def _handle_session_end(bucket: dict, attrs: dict, start) -> None:
    if not start or (bucket['ended_at'] is not None and start <= bucket['ended_at']):
        return
    bucket['ended_at'] = start
    reason = attrs.get('reason')
    if isinstance(reason, str):
        bucket['ended_reason'] = reason


_PER_NAME_HANDLERS = {
    'prompt': _handle_prompt_title,
    'session.title': _handle_session_title,
    'session.start': _handle_session_start,
    'turn': _handle_turn_model,
    'session.end': _handle_session_end,
}


def _span_counter_buckets(spans, duplicates) -> dict:
    """Bucket newly-inserted spans by trace_id into counter deltas.

    Duplicates (already-present (trace_id, span_id) pairs) are skipped
    so an ingest retry never double-counts. Pure function — no I/O.
    """
    buckets: dict = {}
    for span, attrs in spans:
        key = (span.get('trace_id'), span.get('span_id'))
        if key in duplicates:
            continue
        tid = span.get('trace_id')
        if tid is None:
            continue
        bucket = buckets.setdefault(tid, _new_session_bucket())
        name = span.get('name') or ''
        bucket['span_count'] += 1
        _bump_named_counter(bucket, name)
        _apply_test_markers(bucket, attrs)

        start = span.get('start_time')
        end = span.get('end_time') or start
        _update_time_bounds(bucket, start, end)

        # Fallback agent_type from any span attribute, not just
        # session.start. SessionStart isn't guaranteed (resume / compact
        # in some Claude Code versions skips it), but every hook payload
        # already knows the agent — if a handler attached it, take it.
        # The `bucket['agent_type'] is None` guard means this is one
        # branch-not-taken per span after the first hit; session.start
        # still wins because its handler runs below with earliest-time
        # precedence.
        if bucket['agent_type'] is None:
            at = attrs.get('agent_type')
            if isinstance(at, str):
                at_stripped = at.strip()
                if at_stripped:
                    bucket['agent_type'] = at_stripped

        handler = _PER_NAME_HANDLERS.get(name)
        if handler is not None:
            handler(bucket, attrs, start)
    return buckets


_SESSIONS_UPSERT_SQL = """
    INSERT INTO sessions (
        trace_id, title, title_source,
        status, last_start_at, ended_at, ended_reason,
        started_at, last_seen,
        span_count, skill_reads, file_edits, rule_checks,
        plan_enters, prompts, tool_calls, is_test, test_name,
        agent_type, model, cwd
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(trace_id) DO UPDATE SET
        started_at   = MIN(sessions.started_at, excluded.started_at),
        last_seen    = MAX(sessions.last_seen,  excluded.last_seen),
        span_count   = sessions.span_count   + excluded.span_count,
        skill_reads  = sessions.skill_reads  + excluded.skill_reads,
        file_edits   = sessions.file_edits   + excluded.file_edits,
        rule_checks  = sessions.rule_checks  + excluded.rule_checks,
        plan_enters  = sessions.plan_enters  + excluded.plan_enters,
        prompts      = sessions.prompts      + excluded.prompts,
        tool_calls   = sessions.tool_calls   + excluded.tool_calls,
        is_test      = MAX(sessions.is_test, excluded.is_test),
        test_name    = COALESCE(sessions.test_name, excluded.test_name),
        -- Title precedence: user > user_rename > claude_ai_title > first_prompt.
        --   user         — set via the regin API (no UI yet); never auto-replaced
        --   user_rename  — written by Claude `/rename` (`custom-title` line)
        --   claude_ai_title — Claude's auto-generated `ai-title` line; latest wins
        --   first_prompt — derived from the earliest prompt span; fill-only
        title        = CASE
            WHEN sessions.title_source = 'user' THEN sessions.title
            WHEN excluded.title_source = 'user' THEN excluded.title
            WHEN excluded.title_source = 'user_rename' THEN excluded.title
            WHEN sessions.title_source = 'user_rename' THEN sessions.title
            WHEN excluded.title_source = 'claude_ai_title' THEN excluded.title
            WHEN sessions.title_source = 'claude_ai_title' THEN sessions.title
            WHEN sessions.title IS NULL AND excluded.title IS NOT NULL
                THEN excluded.title
            ELSE sessions.title END,
        title_source = CASE
            WHEN sessions.title_source = 'user' THEN sessions.title_source
            WHEN excluded.title_source = 'user' THEN excluded.title_source
            WHEN excluded.title_source = 'user_rename' THEN excluded.title_source
            WHEN sessions.title_source = 'user_rename' THEN sessions.title_source
            WHEN excluded.title_source = 'claude_ai_title' THEN excluded.title_source
            WHEN sessions.title_source = 'claude_ai_title' THEN sessions.title_source
            WHEN sessions.title IS NULL AND excluded.title IS NOT NULL
                THEN excluded.title_source
            ELSE sessions.title_source END,
        last_start_at = COALESCE(MAX(sessions.last_start_at, excluded.last_start_at),
                                 sessions.last_start_at, excluded.last_start_at),
        ended_at      = COALESCE(MAX(sessions.ended_at, excluded.ended_at),
                                 sessions.ended_at, excluded.ended_at),
        ended_reason  = CASE
            WHEN excluded.ended_at IS NOT NULL
                 AND (sessions.ended_at IS NULL
                      OR excluded.ended_at > sessions.ended_at)
                THEN excluded.ended_reason
            ELSE sessions.ended_reason END,
        status        = CASE
            WHEN COALESCE(MAX(sessions.ended_at, excluded.ended_at),
                          sessions.ended_at, excluded.ended_at) IS NOT NULL
                 AND (COALESCE(MAX(sessions.last_start_at, excluded.last_start_at),
                               sessions.last_start_at, excluded.last_start_at) IS NULL
                      OR COALESCE(MAX(sessions.ended_at, excluded.ended_at),
                                  sessions.ended_at, excluded.ended_at)
                          >= COALESCE(MAX(sessions.last_start_at, excluded.last_start_at),
                                      sessions.last_start_at, excluded.last_start_at))
                THEN 'ended'
            WHEN COALESCE(MAX(sessions.last_start_at, excluded.last_start_at),
                          sessions.last_start_at, excluded.last_start_at) IS NOT NULL
                THEN 'active'
            ELSE sessions.status END,
        -- Preserve variant-bracketed ids (e.g. claude-opus-4-7[1m]) from
        -- SessionStart; the transcript-backed `turn` span only carries
        -- the bare base, so don't let a new batch downgrade. Also
        -- reject the placeholder `<synthetic>` (session init /
        -- /compact markers) — it must never overwrite a real id.
        -- Preserve variant-bracketed ids (e.g. claude-opus-4-7[1m]) from
        -- SessionStart; the transcript-backed `turn` span only carries
        -- the bare base, so don't let a new batch downgrade. Also
        -- reject the placeholder `<synthetic>` (session init /
        -- /compact markers) — it must never overwrite a real id.
        agent_type    = COALESCE(sessions.agent_type, excluded.agent_type),
        model         = CASE
            WHEN excluded.model IS NULL THEN sessions.model
            WHEN excluded.model = '<synthetic>'
                 AND sessions.model IS NOT NULL
                 AND sessions.model != '<synthetic>' THEN sessions.model
            WHEN sessions.model IS NULL THEN excluded.model
            WHEN sessions.model LIKE excluded.model || '[%' THEN sessions.model
            ELSE excluded.model END,
        -- Starting cwd: fill-only. The earliest session.start sets it and
        -- a later batch must not clobber it (a /add-dir or cd doesn't
        -- change where the session began).
        cwd           = COALESCE(sessions.cwd, excluded.cwd)
"""


# High-signal span names for repo membership. Reads and Bash are
# deliberately excluded so an incidental cross-repo read never tags a
# session as multi-repo (see `SessionRepo` model docstring).
_REPO_CWD_NAMES = frozenset({'session.start', 'cwd.changed'})
_REPO_EDIT_NAMES = frozenset({'tool.Edit', 'tool.Write', 'tool.apply_patch'})

_SESSION_REPOS_UPSERT_SQL = """
    INSERT INTO session_repos (trace_id, repo_id, is_primary)
    VALUES (?, ?, ?)
    ON CONFLICT(trace_id, repo_id) DO UPDATE SET
        is_primary = MAX(session_repos.is_primary, excluded.is_primary)
"""


def _repo_signal_path(name: str, attrs: dict) -> tuple:
    """Return `(path, is_primary)` for a high-signal span, else `(None, 0)`.

    `session.start` carries the starting cwd (primary); `cwd.changed`
    carries a walked-into cwd; edit spans carry the mutated file path.
    """
    if name == 'session.start':
        return attrs.get('cwd'), 1
    if name == 'cwd.changed':
        return attrs.get('cwd'), 0
    if name in _REPO_EDIT_NAMES:
        return attrs.get('file_path'), 0
    return None, 0


def _active_repos_normalized():
    """Active registered repos, pre-normalized for prefix matching.

    Loaded once per batch; an empty result short-circuits the resolver so
    installs with no registered repos pay nothing.
    """
    from sqlmodel import select as _sel

    from lib.orm import SessionLocal
    from lib.orm.models import Repo
    from lib.rule_engines.repo_scope import normalize_repos

    with SessionLocal() as s:
        repos = s.exec(_sel(Repo).where(Repo.is_active == 1)).all()
    return normalize_repos(repos)


def _resolve_session_repos(conn, normalised, duplicates) -> None:
    """Tag each session in the batch with the registered repos it touched.

    Membership rule lives in the `SessionRepo` model docstring. Delicate
    by design: the repo set is loaded+normalized once, the bulk of spans
    short-circuit on a single name-set check, and writes are bounded by
    the number of distinct repos per session.
    """
    from lib.rule_engines.repo_scope import repo_for_path_norm

    norm = _active_repos_normalized()
    if not norm:
        return
    found: dict = {}
    for span, attrs in normalised:
        if (span.get('trace_id'), span.get('span_id')) in duplicates:
            continue
        name = span.get('name') or ''
        if name not in _REPO_CWD_NAMES and name not in _REPO_EDIT_NAMES:
            continue
        path, primary = _repo_signal_path(name, attrs)
        tid = span.get('trace_id')
        if not path or tid is None:
            continue
        repo = repo_for_path_norm(path, norm)
        if repo is None:
            continue
        key = (tid, repo.id)
        found[key] = max(found.get(key, 0), primary)
    for (tid, repo_id), is_primary in found.items():
        conn.execute(_SESSION_REPOS_UPSERT_SQL, (tid, repo_id, is_primary))


def _counter_buckets_excl_pending(normalised, existing_set) -> dict:
    """`_span_counter_buckets` over only the non-pending spans, so transient
    placeholders never advance the session aggregates."""
    counted = [sa for sa in normalised
               if not is_pending_span_id(sa[0].get('span_id'))]
    return _span_counter_buckets(counted, existing_set)


_TRANSCRIPT_ID_PREFIXES = ('prompt-', 'resp-', 'think-', 'cmd-')


def _infer_source(span_id) -> str:
    """Best-effort capture-source tag for the append-only row: transcript-scan
    spans carry deterministic id prefixes, everything else is a live hook
    event. Audit/debug metadata only — the serve-time merge (lib/trace/merge.py)
    keys on span_id/name, never on this."""
    sid = span_id or ''
    return 'transcript' if sid.startswith(_TRANSCRIPT_ID_PREFIXES) else 'hook'


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
    conn.execute(
        """INSERT INTO session_spans
           (trace_id, span_id, parent_id, name, kind, start_time,
            end_time, duration_ms, attributes, status_code, status_message,
            output_tokens, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
             source = excluded.source""",
        (span.get('trace_id'), span.get('span_id'),
         span.get('parent_id'), span.get('name'),
         span.get('kind', 'internal'), span.get('start_time'),
         span.get('end_time'), span.get('duration_ms'),
         json.dumps(attrs),
         span.get('status_code', 'UNSET'), span.get('status_message'),
         out_tok, _infer_source(span.get('span_id'))),
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


def _resolve_session_status(b: dict) -> str | None:
    """Derive the session status from a counter bucket."""
    if b['ended_at'] and (b['last_start_at'] is None
                          or b['ended_at'] >= b['last_start_at']):
        return 'ended'
    if b['last_start_at']:
        return 'active'
    return None


def _resolve_session_title(b: dict) -> tuple[str | None, str | None]:
    """Derive (title, title_source) from a counter bucket."""
    if b['live_title']:
        return b['live_title'], b['live_title_source']
    if b['title']:
        return b['title'], 'first_prompt'
    return None, None


def _upsert_session_counters(conn, buckets: dict) -> None:
    """Apply incremental sessions-table counter upserts for each trace."""
    for tid, b in buckets.items():
        new_status = _resolve_session_status(b)
        title_val, title_src = _resolve_session_title(b)
        conn.execute(_SESSIONS_UPSERT_SQL, (
            tid, title_val, title_src,
            new_status, b['last_start_at'], b['ended_at'], b['ended_reason'],
            b['started_at'], b['last_seen'],
            b['span_count'], b['skill_reads'], b['file_edits'],
            b['rule_checks'], b['plan_enters'], b['prompts'],
            b['tool_calls'], b['is_test'], b['test_name'],
            b['agent_type'], b['model'], b['cwd'],
        ))


def _refresh_server_side_peaks(conn, normalised: list[tuple]) -> None:
    """Recompute peak_main_context_tokens for any trace this batch touched
    with server-side spans (advisor and similar sub-calls). Spans can arrive
    after the matching turn_usage row has already had its aggregates derived,
    so without this symmetric refresh peak_main would stay stale at peak_full.
    """
    server_side_traces = {
        s.get('trace_id')
        for s, attrs in normalised
        if attrs.get('server_side') is True
    }
    server_side_traces.discard(None)
    for tid in server_side_traces:
        row = conn.execute(
            """
            SELECT MAX(tu.context_used_tokens)
              FROM turn_usage tu
             WHERE tu.trace_id = ?
               AND NOT EXISTS (
                   SELECT 1 FROM session_spans ss
                    WHERE ss.trace_id = tu.trace_id
                      AND COALESCE(ss.turn_uuid,
                                   json_extract(ss.attributes, '$.turn_uuid'))
                          = tu.turn_uuid
                      AND json_extract(ss.attributes, '$.server_side') = 1
               )
            """,
            (tid,),
        ).fetchone()
        conn.execute(
            "UPDATE sessions SET peak_main_context_tokens = ? WHERE trace_id = ?",
            (row[0] if row else None, tid),
        )


def ingest_session_spans(normalised: list[tuple]) -> tuple[int, int]:
    """Persist a batch of normalised spans in one transaction.

    `normalised` is the pre-validated output of `_validate_span` —
    each entry is `(span_dict, attrs_dict)`. Returns
    `(ingested_count, skipped_duplicates)`. Raises on any DB error
    — caller surfaces 500.

    Maintains `sessions` counters incrementally via the module-level
    `_SESSIONS_UPSERT_SQL` so the list view doesn't have to GROUP BY
    every time it's rendered.
    """
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        existing_set, skipped = _detect_existing_spans(conn, normalised)

        conn.execute('BEGIN IMMEDIATE')
        for span, attrs in normalised:
            _insert_span_row(conn, span, attrs)

        # Append-only: placeholders/pending rows are NOT retired here. They
        # coexist with their resolved counterparts and the serve-time merge
        # (lib/trace/merge.py) drops the superseded ones at read time.

        # Pending placeholders carry reserved id prefixes and are skipped by
        # _counter_buckets_excl_pending, so they never advance any aggregate.
        buckets = _counter_buckets_excl_pending(normalised, existing_set)
        _upsert_session_counters(conn, buckets)
        _refresh_server_side_peaks(conn, normalised)

        # Refresh the active-work aggregate for every trace this batch
        # touched. Re-projecting per batch keeps the list view honest
        # without waiting for materialize. Cost scales with spans-per-
        # trace, not session lifetime — typical traces are <1k spans.
        _refresh_active_work_ms(conn, buckets.keys())
        _resolve_session_repos(conn, normalised, existing_set)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    inserted = len(normalised) - skipped
    _trace_log().write(
        'spans_ingested', inserted=inserted, skipped=skipped,
        trace_count=len(buckets),
    )
    return inserted, skipped
