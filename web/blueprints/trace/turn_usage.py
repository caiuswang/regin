"""Trace endpoints — split by URL grouping (skill-reads, mcp-calls, etc.)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from flask import request, jsonify, Response

from lib import hook_plugin as _hp
from lib.orm import SessionLocal
from lib.orm.models import (
    PlanSession, PromptImage, RuleTrigger, Session as SessionModel,
    SessionSpan, SkillRead,
)
from lib.utils.pagination import clamp_size, keyset_page_stmt
from lib.trace import trace_service
from web.helpers import (
    _is_non_blank_str, _is_iso_timestamp, _normalize_is_test,
    _ingest_max_batch_size, _ingest_max_attributes_bytes,
    _IS_TEST_WHERE, _IS_TEST_CASE,
)
# NOTE: `_INGEST_DEDUP_WINDOW_SEC` is looked up at *call* time via the
# module (not imported by value) so tests can monkeypatch
# `web.helpers._INGEST_DEDUP_WINDOW_SEC` and have the handler observe the
# new window. `from web import helpers as _helpers` preserves the live
# reference; doing `from web.helpers import _INGEST_DEDUP_WINDOW_SEC`
# would bind the initial value forever.
from web import helpers as _helpers
from lib.trace.projection import (
    _fetch_spans, _graft_orphans, _widen_envelopes,
    _build_span_tree, _persist_projection,
)

from web.blueprints.trace import trace_bp


# ── Per-turn token usage ingest + read ─────────────────────────

@trace_bp.route('/api/turn-usage', methods=['POST'])
def api_ingest_turn_usage():
    """Upsert per-assistant-turn token rows.

    Body: a row dict or list of row dicts. Each row must carry
    `trace_id`, `turn_uuid`, `timestamp`. Dedup is handled at the DB
    layer via (trace_id, turn_uuid) PK, so replaying the full
    transcript on every hook is idempotent.
    """
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({'ok': False, 'error': 'invalid JSON body'}), 400
    if not isinstance(data, list):
        data = [data]
    try:
        inserted, skipped = trace_service.ingest_turn_usage(data)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({
        'ok': True,
        'ingested': inserted,
        'skipped_malformed': skipped,
    })


@trace_bp.route('/api/turn-usage/tool-attribution', methods=['POST'])
def api_ingest_tool_attribution():
    """Attach per-tool token estimates to existing tool.* spans.

    Body: `{trace_id, turn_uuid, tool_calls: [{tool_use_id, name?,
            output_tokens, input_tokens, image_tokens?}]}`.

    Matches by `tool_use_id` against the span's column or its
    `attributes.tool_use_id`. `cost_usd` is computed from
    `sessions.model` at update time. See
    `trace_service.ingest_tool_attribution` for the full contract.
    """
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({'ok': False, 'error': 'invalid JSON body'}), 400
    try:
        updated, skipped = trace_service.ingest_tool_attribution(data)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({'ok': True, 'updated': updated, 'skipped': skipped})


@trace_bp.route('/api/session-status', methods=['POST'])
def api_ingest_session_status():
    """Persist an authoritative model + context snapshot for a session.

    Body: `{trace_id, model?, context_used_tokens?, context_window_tokens?}`.

    Posted by `scripts/regin-statusline`, an opt-in entry point users
    wire up as their Claude Code `statusLine.command`. Claude Code's
    hook payloads do not carry the model variant suffix (e.g. `[1m]`)
    and the transcript strips it too, so the statusline feed is the
    only runtime source regin has for the real context-window total.

    The endpoint itself is independent of the shipped script — any
    caller that can assemble the body may POST. The service layer
    preserves a previously-stored variant-bracketed model id against
    a bare incoming one, and takes `MAX` for the peak.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'ok': False, 'error': 'body must be an object'}), 400
    trace_id = data.get('trace_id')
    if not isinstance(trace_id, str) or not trace_id:
        return jsonify({'ok': False, 'error': 'trace_id is required'}), 400
    model = data.get('model')
    if model is not None and not isinstance(model, str):
        return jsonify({'ok': False, 'error': 'model must be a string'}), 400
    used = data.get('context_used_tokens')
    total = data.get('context_window_tokens')
    for field, v in (('context_used_tokens', used),
                     ('context_window_tokens', total)):
        if v is not None and not isinstance(v, int):
            return jsonify({'ok': False,
                            'error': f'{field} must be an integer'}), 400
    try:
        trace_service.ingest_session_status(
            trace_id=trace_id,
            model=model,
            context_used_tokens=used,
            context_window_tokens=total,
        )
    except Exception as exc:
        return jsonify({
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({'ok': True})


@trace_bp.route('/api/sessions/<trace_id>/tool-rollup')
def api_session_tool_rollup(trace_id):
    """Per-tool token rollup for one session.

    Returns `{rollup: [{name, calls, input_tokens, output_tokens,
    image_tokens, cost_usd}, ...]}` plus the session-level totals: the
    `attributed_*` per-tool sums, the recorded main-model `session_*` token
    aggregate (incl. `session_cache_read_tokens` /
    `session_cache_creation_tokens` / `session_total_tokens`), the per-bucket
    `*_cost_usd` dollar split, the `subagent_*` server-side sub-model spend
    (the advisor, excluded from `session_cost_usd`), the `total_spend_*`
    (main bill + sub-agent = true spend) denominator, and the `untagged_*`
    output remainder. The frontend uses these to reconcile the panel to true
    spend (cache dominates tokens but not cost). SUM in SQL so we don't ship
    hundreds of spans just for the bar chart.
    """
    rollup, totals = trace_service.fetch_tool_token_rollup(trace_id)
    return jsonify({'rollup': rollup, **totals})


@trace_bp.route('/api/sessions/<trace_id>/turn-usage')
def api_session_turn_usage(trace_id):
    """Return per-turn usage rows for a session, oldest-first.

    Each turn row is augmented with:
    - `duration_ms` — ms since the previous turn (null for the first).
    - `ctx_pct`     — context_used_tokens as a % of the session window.

    Also includes `max_consumption_tokens` — the largest
    (input_tokens + cache_creation_tokens + output_tokens) seen across
    all turns — so the frontend can scale per-row consumption bars
    without a client-side reduce.
    """
    from datetime import datetime as _dt
    from sqlmodel import select as _select
    from lib.tokens.model_windows import infer_window as _infer_window

    rows = trace_service.fetch_turn_usage(trace_id)

    # Fetch context window once for ctx_pct computation. Window
    # inference uses the all-inclusive peak so the 1M-variant promotion
    # heuristic still fires on sessions whose main flow never crosses
    # 200K but ran on `[1m]` (the advisor rollup pushes peak past 200K).
    with SessionLocal() as db:
        sess_row = db.exec(
            _select(SessionModel.model,
                    SessionModel.peak_context_tokens,
                    SessionModel.peak_main_context_tokens)
            .where(SessionModel.trace_id == trace_id)
        ).first()
    window = None
    peak_full = None
    peak_main = None
    if sess_row:
        model, peak_full, peak_main = sess_row
        if isinstance(peak_full, int):
            window = _infer_window(model, peak_full)

    def _parse_iso(s):
        if not s:
            return None
        try:
            return _dt.fromisoformat(s.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return None

    max_consumption = 0
    for i, t in enumerate(rows):
        c = (t.get('input_tokens') or 0) + (t.get('cache_creation_tokens') or 0) + (t.get('output_tokens') or 0)
        if c > max_consumption:
            max_consumption = c

        if i > 0:
            t0 = _parse_iso(rows[i - 1].get('timestamp'))
            t1 = _parse_iso(t.get('timestamp'))
            t['duration_ms'] = max(0, int((t1 - t0).total_seconds() * 1000)) if t0 and t1 else None
        else:
            t['duration_ms'] = None

        ctx = t.get('context_used_tokens')
        if isinstance(ctx, int) and window and window > 0:
            t['ctx_pct'] = round(max(0.0, min(100.0, ctx * 100.0 / window)), 1)
        else:
            t['ctx_pct'] = None

    return jsonify({
        'trace_id': trace_id,
        'turns': rows,
        'max_consumption_tokens': max_consumption,
        'peak_context_tokens': peak_full,
        'peak_main_context_tokens': peak_main,
        'context_window_tokens': window,
    })


