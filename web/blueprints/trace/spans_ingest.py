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


# ── Session-span ingest (transactional batch) ──────────────────

def _validate_span(span, max_attr_bytes: int) -> tuple[dict | None, str | None]:
    """Validate one span dict.

    On success returns (normalised_attrs, None). On failure returns
    (None, reason). The normalised_attrs dict is the span's `attributes`
    with `is_test` coerced to a clean JSON boolean and no ambiguous
    string/int variants (see `_normalize_is_test`).

    Pulled out of the ingest loop so the handler body stays a thin
    dispatcher — validation rules live here, transaction management in
    the caller.
    """
    if not isinstance(span, dict):
        return None, 'span must be an object'
    for required in ('trace_id', 'span_id', 'name'):
        if not _is_non_blank_str(span.get(required)):
            return None, f'missing or blank {required}'
    # start_time is mandatory and must parse — downstream
    # (_widen_envelopes, ORDER BY) assumes ISO 8601.
    if not _is_iso_timestamp(span.get('start_time')):
        return None, 'start_time must be a valid ISO 8601 timestamp'
    # end_time is optional, but if present must also parse.
    end_time = span.get('end_time')
    if end_time not in (None, '') and not _is_iso_timestamp(end_time):
        return None, 'end_time must be a valid ISO 8601 timestamp'
    attrs = span.get('attributes') or {}
    if not isinstance(attrs, dict):
        return None, 'attributes must be an object'
    if 'is_test' in attrs:
        if _normalize_is_test(attrs['is_test']):
            attrs['is_test'] = True
        else:
            attrs.pop('is_test', None)
    # Size-cap the serialized attributes. This prevents one bad
    # hook from writing a multi-megabyte TEXT blob that slows
    # every subsequent json_extract() on that row.
    try:
        attr_size = len(json.dumps(attrs).encode('utf-8'))
    except (TypeError, ValueError) as exc:
        return None, f'attributes not JSON-serializable: {exc}'
    if attr_size > max_attr_bytes:
        return None, (f'attributes too large: {attr_size} bytes '
                      f'(max: {max_attr_bytes})')
    return attrs, None


@trace_bp.route('/api/session-spans', methods=['POST'])
def api_ingest_session_span():
    """Ingest one or more session spans atomically.

    Body: a span dict, or a list of span dicts. Each span must carry
    `trace_id`, `span_id`, `name`, `start_time`. Any `attributes.is_test`
    is normalised to a real JSON boolean at write time so downstream
    filters don't have to juggle {1, "true", "True", "yes"}.

    The whole batch is wrapped in a single transaction; any malformed
    span aborts the batch and rolls back. Response:
      { ok, ingested, skipped_duplicates, errors: [{index, reason}] }
    """
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({'ok': False, 'error': 'invalid JSON body'}), 400
    if not isinstance(data, list):
        data = [data]

    max_batch = _ingest_max_batch_size()
    if len(data) > max_batch:
        return jsonify({
            'ok': False,
            'error': f'batch too large: {len(data)} spans (max: {max_batch})',
        }), 413

    max_attr_bytes = _ingest_max_attributes_bytes()
    errors = []
    normalised = []
    for i, span in enumerate(data):
        attrs, reason = _validate_span(span, max_attr_bytes)
        if reason is not None:
            errors.append({'index': i, 'reason': reason})
            continue
        normalised.append((span, attrs))

    if errors:
        return jsonify({'ok': False, 'ingested': 0, 'errors': errors}), 400

    try:
        ingested, skipped = trace_service.ingest_session_spans(normalised)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'ingested': 0,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500

    return jsonify({
        'ok': True,
        'ingested': ingested,
        'skipped_duplicates': skipped,
    })

