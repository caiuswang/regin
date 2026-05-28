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


# ── MCP tool calls dashboard ───────────────────────────────────

@trace_bp.route('/api/mcp-calls')
def api_mcp_calls():
    """Keyset-paginated MCP tool-call dashboard.

    Filters down session_spans to rows whose name starts 'tool.mcp__'.
    Cursor is (start_time DESC, span_id DESC) — unique per span, no
    collision on identical timestamps.

    Service-layer extraction: SQL lives in
    `lib.trace.trace_service.list_mcp_calls_page`.
    """
    tool_filter = request.args.get('tool')
    session_filter = request.args.get('session')
    include_tests = request.args.get('include_tests', 'false').lower() in ('1', 'true', 'yes')
    cursor_token = request.args.get('cursor')
    size = clamp_size(request.args.get('size'), default=100)

    page, stats, sessions = trace_service.list_mcp_calls_page(
        tool_filter=tool_filter,
        session_filter=session_filter,
        include_tests=include_tests,
        cursor_token=cursor_token,
        size=size,
    )
    envelope = page.to_envelope()
    if cursor_token is None:
        envelope['stats'] = stats
        envelope['sessions'] = sessions
    envelope['tool_filter'] = tool_filter
    envelope['session_filter'] = session_filter
    envelope['include_tests'] = include_tests
    return jsonify(envelope)


