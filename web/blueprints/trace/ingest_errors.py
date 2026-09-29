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


# ── Ingest-errors observability ────────────────────────────────

@trace_bp.route('/api/ingest-errors')
def api_ingest_errors():
    """Surface the hook-plugin ingest error log so operators can see
    drop patterns without tailing ~/.claude/traces/ingest-errors.jsonl.

    Query params:
      limit    — max rows returned (default 50, hard-capped at 1000).
      endpoint — filter to rows where `endpoint == <value>`.
      gave_up  — filter to 'true' or 'false' (string comparison).

    Returns aggregations (by endpoint / error_type / gave_up) based
    on ALL rows read from the tail, not just the filtered subset, so
    operators can compare filtered signal against total volume.
    """
    limit = _ingest_errors_limit(request.args.get('limit', '50'))
    path = _hp._INGEST_ERROR_LOG
    rows = _read_ingest_error_rows(path)
    rows.reverse()  # most-recent first
    filtered = _filter_ingest_error_rows(
        rows, request.args.get('endpoint'),
        request.args.get('gave_up'))  # 'true' | 'false' | None

    return jsonify({
        'path': path,
        'total_read': len(rows),
        'returned': min(len(filtered), limit),
        'rows': filtered[:limit],
        **_ingest_error_aggregates(rows),
    })


def _ingest_errors_limit(limit_raw) -> int:
    try:
        return max(1, min(1000, int(limit_raw)))
    except (TypeError, ValueError):
        return 50


def _read_ingest_error_lines(path) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r') as f:
            # Small logs read fully; cap read by tailing last
            # 4000 lines to keep this cheap even on large files.
            return f.readlines()[-4000:]
    except OSError:
        return []


def _read_ingest_error_rows(path) -> list:
    rows = []
    for ln in _read_ingest_error_lines(path):
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except (ValueError, TypeError):
            continue
    return rows


def _ingest_error_aggregates(rows) -> dict:
    by_endpoint: dict[str, int] = {}
    by_error_type: dict[str, int] = {}
    by_gave_up = {'true': 0, 'false': 0}
    for r in rows:
        ep = r.get('endpoint') or 'unknown'
        by_endpoint[ep] = by_endpoint.get(ep, 0) + 1
        et = r.get('error_type') or 'unknown'
        by_error_type[et] = by_error_type.get(et, 0) + 1
        key = 'true' if r.get('gave_up') else 'false'
        by_gave_up[key] += 1
    return {
        'by_endpoint': by_endpoint,
        'by_error_type': by_error_type,
        'by_gave_up': by_gave_up,
    }


def _filter_ingest_error_rows(rows, endpoint_filter, gave_up_filter) -> list:
    filtered = rows
    if endpoint_filter:
        filtered = [r for r in filtered if r.get('endpoint') == endpoint_filter]
    if gave_up_filter in ('true', 'false'):
        want = gave_up_filter == 'true'
        filtered = [r for r in filtered if bool(r.get('gave_up')) is want]
    return filtered
