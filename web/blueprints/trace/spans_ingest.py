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


def _bind_staged_attachments(normalised) -> None:
    """Settle composer-staged images onto the prompt spans about to be written.

    A prompt typed in the browser reaches the agent before its span exists, so
    the images it carried are still sitting in `bridge_attachments` waiting to
    learn which span they belong to. The `[Image #N]` markers in the span's own
    text are the answer, and they are only readable here.

    Runs BEFORE the insert so the indices can ride along in `attributes`.
    `image_indices` is what the reader keys off — the bytes being in
    `prompt_images` is not enough on its own, because nothing would ever ask
    for them. Existing indices are preserved and merged: a prompt can carry
    both a terminal paste (numbered by the CLI's own cache) and a composer
    one, and dropping either would blank a thumbnail that has real bytes
    behind it.

    Never allowed to fail the ingest: a span is the trace's record of what
    happened, and losing one to a thumbnail is a bad trade.
    """
    from lib.agent_bridge import attachments

    for span, attrs in normalised:
        if span.get('name') != 'prompt' or not isinstance(attrs, dict):
            continue
        text = attrs.get('text')
        if not isinstance(text, str) or not text:
            continue
        try:
            attachments.bind_prompt_span(span.get('trace_id'),
                                         span.get('span_id'), text)
            # Read back rather than trusting what THIS call bound: a re-posted
            # span binds nothing (the copy already exists) and the ingest
            # upserts attributes wholesale, so a stamp derived from the return
            # value would blank itself on the transcript's second pass.
            settled = attachments.settled_indices(span.get('trace_id'),
                                                  span.get('span_id'))
        except Exception:  # noqa: BLE001 — a thumbnail never costs a span
            from lib.activity_log import get_activity_logger
            get_activity_logger("trace_ingest").error(
                "bridge_attachment_bind_failed",
                trace_id=span.get('trace_id'), exc_info=True)
            continue
        if not settled:
            continue
        existing = attrs.get('image_indices')
        merged = set(existing if isinstance(existing, list) else []) | set(settled)
        attrs['image_indices'] = sorted(merged)


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

    _bind_staged_attachments(normalised)

    try:
        ingested, skipped = trace_service.ingest_session_spans(normalised)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'ingested': 0,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500

    if ingested:
        _announce_spans(normalised)
        _announce_clears(normalised)

    return jsonify({
        'ok': True,
        'ingested': ingested,
        'skipped_duplicates': skipped,
    })


def _announce_clears(normalised) -> None:
    """Raise the succession frame for every `/clear` this batch recorded.

    The ingest that commits `ended_reason = 'clear'` is one of the two sites
    that can be the last to learn a succession — the other is the heir's pane
    registration. `succession.announce` resolves and stays silent when the
    other half has not landed yet.

    Best-effort like `_announce_spans`, and for the same reason: a hook is
    blocked on this POST.
    """
    try:
        from lib.agent_bridge import succession

        for span, attrs in normalised:
            if span.get('name') != 'session.end':
                continue
            if (attrs or {}).get('reason') != 'clear':
                continue
            # The RAW trace id, deliberately unlike `_announce_spans`: that one
            # canonicalises because the reader is keyed on the group's id,
            # while `bridge_panes` and `sessions.ended_reason` are keyed on the
            # id the hook reported. Canonicalising here would look up a pane
            # nobody registered.
            trace_id = span.get('trace_id')
            if trace_id:
                succession.announce(trace_id)
    except Exception:  # noqa: BLE001 - a wake-up must never fail an ingest
        return


def _announce_spans(normalised) -> None:
    """Tell the streams reading a trace that it just grew.

    One frame per distinct trace in the batch, never per span, and keyed on the
    canonical id: a run's spans land under its `sdk-` or child alias while the
    view showing it is keyed on the group's canonical id, so the raw
    `trace_id` would name a session nobody is reading. That same canonical id
    is the frame's `trace:` topic, so a tab reading another session is not
    woken at all rather than woken and filtering.

    Best-effort by construction. This runs inside an ingest a hook is blocking
    on, so a fan-out failure costs the caller nothing.
    """
    from lib.notifications import hub
    from lib.trace.alias import canonical_trace_id

    from lib.trace.trace_service import projection_cache

    # span ids per canonical trace, so a reader holding this batch's spans can
    # refresh exactly them instead of re-fetching every body under the tail.
    moved: dict[str, list] = {}
    for span, _attrs in normalised:
        trace_id = span.get('trace_id')
        if not trace_id:
            continue
        try:
            canonical = canonical_trace_id(trace_id) or trace_id
        except Exception:  # noqa: BLE001 - a wake-up must never fail an ingest
            return
        span_id = span.get('span_id')
        if span_id:
            moved.setdefault(canonical, []).append(span_id)
        else:
            moved.setdefault(canonical, [])

    # Resolving each group costs a query, and this runs inside an ingest a hook
    # is blocking on. With nobody watching there is no frame to build.
    if not moved or not hub.subscriber_count():
        return

    from lib.orm.engine import get_connection
    from lib.trace.alias import trace_group

    try:
        conn = get_connection()
        try:
            # Summed over the alias group, exactly as `/map` reports it — the
            # reader compares the two, so a frame quoting one member's counter
            # would read as a permanent mismatch and cost the session its
            # cheap path for good.
            groups = {c: trace_group(conn, c) for c in moved}
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - a wake-up must never fail an ingest
        return

    for canonical, span_ids in moved.items():
        try:
            hub.broadcast_event(hub.TRACE_SPANS_EVENT, {
                'trace_id': canonical,
                'epoch': projection_cache.epoch(groups[canonical]),
                # Every span this batch touched. The reader checks that the
                # epoch moved by exactly this many, so a change no frame names
                # cannot hide inside the next one.
                'span_ids': span_ids,
            }, topic=f'trace:{canonical}')
        except Exception:  # noqa: BLE001 - a wake-up must never fail an ingest
            return

