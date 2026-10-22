"""Write-side ingest for per-turn usage, per-tool token attribution, the
session statusline snapshot, and the session-model backfill.
"""

from __future__ import annotations

from lib.trace.trace_service.session_buckets import (
    _PER_NAME_HANDLERS,
    _handle_session_start,
    _handle_transcript_model,
    _is_less_specific_model,
    _new_session_bucket,
)
from lib.trace.trace_service.span_rows import _invalidate_projection, _trace_log


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
            cost_usd, effort_level, reasoning_tokens
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                                            turn_usage.effort_level),
            reasoning_tokens      = COALESCE(excluded.reasoning_tokens,
                                            turn_usage.reasoning_tokens)
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
            r.get('reasoning_tokens'),
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


def _turn_usage_model(conn, tid: str) -> str | None:
    """Newest non-empty `turn_usage.model` for a session, walked back to the
    most specific id the rows hold.

    Bare base id (no `[1m]` suffix), so callers must only ever use it as a
    fallback — `_is_less_specific_model` guards the span-borne path from
    being downgraded by it. The walk applies that same guard within these
    rows: a turn that completes before any assistant message names a model is
    priced against the launch alias, and newest-wins alone would let that
    alias shadow the resolved id an earlier turn recorded.
    """
    rows = conn.execute("""
        SELECT model FROM turn_usage
         WHERE trace_id = ? AND model IS NOT NULL AND model <> ''
         ORDER BY turn_index DESC
    """, (tid,)).fetchall()
    best = None
    for (model,) in rows:
        if best is None or _is_less_specific_model(best, model):
            best = model
    return best


def _refresh_session_aggregates(conn, trace_ids) -> None:
    """Re-derive session-row aggregates from turn_usage so the
    header/list views stay authoritative."""
    for tid in trace_ids:
        _refresh_one_session_aggregates(conn, tid)


def _main_turn_rows(conn, tid: str) -> list:
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
    return conn.execute("""
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


def _session_model(conn, tid: str):
    # Read sessions.model (may be None for a freshly-ingesting
    # session) to compute the window; infer_window handles the
    # None case gracefully.
    sess = conn.execute(
        "SELECT model FROM sessions WHERE trace_id = ?", (tid,)
    ).fetchone()
    model = (sess[0] if sess else None) or None
    # A live session has no model yet: the only span-borne carriers
    # (`session.start`, `turn`, `assistant_response`) arrive with
    # transcript replay, while `turn_usage` — already open here — has
    # carried the model from the first turn. Without this the window
    # falls back to 200K mid-session and every ctx% reads inflated
    # (a 1M-window model showed 5x its real usage) until the session
    # ends. Persisted by the caller so read-time window inference agrees.
    return model or _turn_usage_model(conn, tid)


def _refresh_one_session_aggregates(conn, tid: str) -> None:
    from lib.tokens.model_windows import infer_window

    row = conn.execute("""
        SELECT SUM(input_tokens),  SUM(output_tokens),
               SUM(cache_read_tokens), SUM(cache_creation_tokens),
               MAX(context_used_tokens),
               COUNT(*),
               SUM(cost_usd)
        FROM turn_usage WHERE trace_id = ?
    """, (tid,)).fetchone()
    if not row or row[5] == 0:
        return
    (in_tot, out_tot, cread, ccreate, peak, _cnt, cost_tot) = row
    main_rows = _main_turn_rows(conn, tid)
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
    model = _session_model(conn, tid)
    # Window inference still uses the all-inclusive peak — the
    # 1M-variant promotion heuristic needs to see advisor-inflated
    # turns to fire on sessions whose main flow never crosses 200K
    # but ran on `[1m]`.
    window = infer_window(model, int(peak or 0))
    conn.execute("""
        UPDATE sessions SET
            input_tokens = ?, output_tokens = ?,
            cache_read_tokens = ?, cache_creation_tokens = ?,
            peak_context_tokens = ?,
            peak_main_context_tokens = ?,
            live_context_tokens = ?,
            context_window_tokens = ?,
            cost_usd = ?,
            model = COALESCE(model, ?)
        WHERE trace_id = ?
    """, (in_tot, out_tot, cread, ccreate, peak, peak_main, live, window,
          cost_tot, model, tid))


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
    left untouched. `duration_ms` and `source_prompt_id` follow the same
    name scoping and are fill-only: a hook payload that reported the tool's
    own execution time (Claude) is authoritative and outranks the
    transcript's record-to-record bracket."""
    from lib.tokens.pricing import TokenBreakdown, cost
    if not isinstance(tc, dict):
        return False
    tu_id = tc.get('tool_use_id')
    if not isinstance(tu_id, str) or not tu_id:
        return False
    out_tok = _as_int_or_none(tc.get('output_tokens'))
    in_tok = _as_int_or_none(tc.get('input_tokens'))
    img_tok = _as_int_or_none(tc.get('image_tokens'))
    dur_ms = _as_int_or_none(tc.get('duration_ms'))
    src_prompt = tc.get('source_prompt_id')
    if not isinstance(src_prompt, str) or not src_prompt:
        src_prompt = None
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
                                    ELSE parent_id END,
               duration_ms   = CASE WHEN name LIKE 'tool.%'
                                     AND COALESCE(duration_ms, 0) = 0
                                    THEN COALESCE(?, duration_ms)
                                    ELSE duration_ms END,
               source_prompt_id = CASE WHEN name LIKE 'tool.%'
                                       THEN COALESCE(source_prompt_id, ?)
                                       ELSE source_prompt_id END
         WHERE trace_id = ?
           AND (tool_use_id = ?
                OR json_extract(attributes, '$.tool_use_id') = ?)
        """,
        (out_tok, in_tok, img_tok, usd, tu_id, turn_uuid, parent_span_id,
         dur_ms, src_prompt, trace_id, tu_id, tu_id),
    )
    if cur.rowcount > 0:
        # Token/cost columns ride the projection out to the view, and this
        # UPDATE moves them without touching any row id.
        _invalidate_projection(trace_id)
    return cur.rowcount > 0


def _parse_attribution_payload(payload) -> tuple | None:
    """(trace_id, turn_uuid, tool_calls, parent_span_id) from a
    tool-attribution body, or None when it is unusable."""
    if not isinstance(payload, dict):
        return None
    trace_id = payload.get('trace_id')
    tool_calls = payload.get('tool_calls')
    if not isinstance(trace_id, str) or not trace_id:
        return None
    if not isinstance(tool_calls, list):
        return None
    turn_uuid = payload.get('turn_uuid')
    # Issuing turn's resp-/think- span (or None). Backfills the live
    # tool.* span's parent_id, which is posted parent-less at PostToolUse
    # time. Name-scoped in SQL so it never disturbs the parentage of a
    # permission.request / task.notification row that happens to share a
    # tool_use_id.
    parent_span_id = payload.get('parent_span_id')
    return (
        trace_id,
        turn_uuid if isinstance(turn_uuid, str) else None,
        tool_calls,
        parent_span_id if isinstance(parent_span_id, str) and parent_span_id else None,
    )


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
            name?, output_tokens, input_tokens, image_tokens?, duration_ms?,
            source_prompt_id?}]}`.

    Returns (updated_count, skipped_count).
    """
    from lib.orm.engine import get_connection

    parsed = _parse_attribution_payload(payload)
    if parsed is None:
        return 0, 1
    trace_id, turn_uuid, tool_calls, parent_span_id = parsed

    conn = get_connection()
    try:
        sess_row = conn.execute(
            "SELECT model FROM sessions WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        model = sess_row[0] if sess_row else None
        turn_ctx = _turn_context_tokens(conn, trace_id, turn_uuid)

        attributed = [
            _attribute_one_call(
                conn, tc, model=model, turn_ctx=turn_ctx, turn_uuid=turn_uuid,
                parent_span_id=parent_span_id, trace_id=trace_id,
            )
            for tc in tool_calls
        ]
        updated = sum(1 for ok in attributed if ok)
        skipped = len(attributed) - updated
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


def _merged_status_model(incoming, current):
    # Keep the richer id when the incoming one is a bare base.
    if not (isinstance(incoming, str) and incoming):
        return current
    return current if _is_less_specific_model(incoming, current) else incoming


def _merged_status_peak(incoming, current):
    if not isinstance(current, int):
        return incoming
    if not isinstance(incoming, int) or incoming < current:
        return current
    return incoming


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

        model = _merged_status_model(model, row['model'])
        peak = _merged_status_peak(context_used_tokens, row['peak_context_tokens'])

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


def _derive_model_from_spans(conn, trace_id: str) -> str | None:
    """Recompute a session's model from its stored model-carrying spans
    using the exact live-ingest precedence (the `_PER_NAME_HANDLERS`
    handlers on a throwaway bucket), so the backfill can never drift from
    live ingest. `session.start` contributes its variant-bracketed id and
    `turn`/`assistant_response` the transcript's bare base."""
    import json as _json

    model_names = tuple(
        n for n, h in _PER_NAME_HANDLERS.items()
        if h in (_handle_transcript_model, _handle_session_start)
    )
    placeholders = ",".join("?" * len(model_names))
    rows = conn.execute(
        f"SELECT name, attributes, start_time FROM session_spans "
        f"WHERE trace_id = ? AND name IN ({placeholders}) ORDER BY start_time",
        (trace_id, *model_names),
    ).fetchall()
    bucket = _new_session_bucket()
    for row in rows:
        try:
            attrs = _json.loads(row['attributes']) if row['attributes'] else {}
        except (ValueError, TypeError):
            attrs = {}
        handler = _PER_NAME_HANDLERS.get(row['name'])
        if handler is not None:
            handler(bucket, attrs, row['start_time'])
    return bucket['model']


def backfill_session_models(only_missing: bool = True, limit: int = 0) -> dict:
    """Set `sessions.model` from stored transcript spans for sessions that
    never resolved one at ingest time — transcript-replayed `llm-stage`
    runs whose model landed only on `assistant_response` spans, which the
    aggregation ignored before that handler was registered.

    Idempotent: re-derives and re-applies via `ingest_session_status`,
    whose precedence guard won't downgrade a variant-bracketed id. The
    context window is derived from `model` at read time, so setting the
    model is enough to fix the context %/window too.
    """
    from lib.orm.engine import get_connection

    conn = get_connection()
    try:
        where = "WHERE model IS NULL" if only_missing else ""
        candidates = conn.execute(
            f"SELECT trace_id, model FROM sessions {where}"
        ).fetchall()
        counts = {'updated': 0, 'unchanged': 0, 'no_model': 0}
        for row in candidates:
            if limit and counts['updated'] >= limit:
                break
            derived = _derive_model_from_spans(conn, row['trace_id'])
            if not derived:
                counts['no_model'] += 1
            elif derived == row['model']:
                counts['unchanged'] += 1
            else:
                ingest_session_status(row['trace_id'], model=derived)
                counts['updated'] += 1
    finally:
        conn.close()
    return counts
