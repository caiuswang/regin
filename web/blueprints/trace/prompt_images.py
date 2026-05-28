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


# ── Prompt-image ingest + serve ────────────────────────────────

# Per-image hard cap on the ingest path. The setting
# `prompt_image_max_bytes` is what the *hook* uses to decide whether to
# even POST; this server-side ceiling guards the case of a misbehaving
# client. Sized at 20 MB so legitimate high-resolution screenshots
# (~10–15 MB base64) still land.
_PROMPT_IMAGE_INGEST_MAX_BYTES = 20 * 1024 * 1024

_ALLOWED_IMAGE_MEDIA_TYPES = frozenset({
    'image/png', 'image/jpeg', 'image/gif', 'image/webp',
})


@trace_bp.route('/api/prompt-images', methods=['POST'])
def api_ingest_prompt_images():
    """Upsert one or more user-submitted prompt images.

    Body: list of `{trace_id, prompt_span_id, idx, media_type, data_b64}`.
    Idempotent on the (trace_id, prompt_span_id, idx) primary key — a
    replay of a transcript that already contributed images is a no-op.
    """
    import base64
    import hashlib
    from sqlalchemy import select as _select

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({'ok': False, 'error': 'invalid JSON body'}), 400
    if not isinstance(data, list):
        data = [data]

    errors = []
    rows: list[dict] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append({'index': i, 'reason': 'not an object'})
            continue
        trace_id = item.get('trace_id')
        span_id = item.get('prompt_span_id')
        idx = item.get('idx')
        media_type = item.get('media_type')
        data_b64 = item.get('data_b64')
        if not isinstance(trace_id, str) or not trace_id:
            errors.append({'index': i, 'reason': 'trace_id required'})
            continue
        if not isinstance(span_id, str) or not span_id:
            errors.append({'index': i, 'reason': 'prompt_span_id required'})
            continue
        if not isinstance(idx, int) or idx < 1:
            errors.append({'index': i, 'reason': 'idx must be a positive int'})
            continue
        if media_type not in _ALLOWED_IMAGE_MEDIA_TYPES:
            errors.append({'index': i, 'reason': f'unsupported media_type {media_type!r}'})
            continue
        if not isinstance(data_b64, str) or not data_b64:
            errors.append({'index': i, 'reason': 'data_b64 required'})
            continue
        try:
            raw = base64.b64decode(data_b64, validate=False)
        except (ValueError, TypeError) as exc:
            errors.append({'index': i, 'reason': f'bad base64: {exc}'})
            continue
        if len(raw) > _PROMPT_IMAGE_INGEST_MAX_BYTES:
            errors.append({'index': i,
                           'reason': f'image too large: {len(raw)} > {_PROMPT_IMAGE_INGEST_MAX_BYTES}'})
            continue
        rows.append({
            'trace_id': trace_id,
            'prompt_span_id': span_id,
            'idx': idx,
            'media_type': media_type,
            'bytes_': raw,
            'byte_size': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest(),
        })

    if errors:
        return jsonify({'ok': False, 'ingested': 0, 'errors': errors}), 400

    ingested = 0
    skipped = 0
    try:
        with SessionLocal() as session:
            for row in rows:
                existing = session.execute(
                    _select(PromptImage).where(
                        PromptImage.trace_id == row['trace_id'],
                        PromptImage.prompt_span_id == row['prompt_span_id'],
                        PromptImage.idx == row['idx'],
                    )
                ).first()
                if existing is not None:
                    skipped += 1
                    continue
                session.add(PromptImage(**row))
                ingested += 1
            session.commit()
    except Exception as exc:
        return jsonify({
            'ok': False,
            'ingested': 0,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500

    return jsonify({'ok': True, 'ingested': ingested,
                    'skipped_duplicates': skipped})


@trace_bp.route('/api/sessions/<trace_id>/prompts/<span_id>/images/<int:idx>')
def api_get_prompt_image(trace_id, span_id, idx):
    """Serve a single user-submitted image as raw bytes."""
    from sqlalchemy import select as _select
    with SessionLocal() as session:
        row = session.execute(
            _select(PromptImage).where(
                PromptImage.trace_id == trace_id,
                PromptImage.prompt_span_id == span_id,
                PromptImage.idx == idx,
            )
        ).first()
    if row is None:
        return jsonify({'error': 'image not found'}), 404
    img: PromptImage = row[0]
    # Cache aggressively: the (trace, span, idx) tuple is immutable once
    # written — a hash mismatch would be a different idx, not a rewrite.
    return Response(
        img.bytes_,
        mimetype=img.media_type,
        headers={'Cache-Control': 'private, max-age=86400'},
    )

