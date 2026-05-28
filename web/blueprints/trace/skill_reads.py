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


# ── Skill-reads ingest + dashboard ─────────────────────────────

@trace_bp.route('/api/skill-reads', methods=['POST'])
def api_ingest_skill_read():
    """Record a Claude skill-file read event.

    Dedup: if the exact same (session_id, skill_id, file_path) was
    ingested within the last `_INGEST_DEDUP_WINDOW_SEC`, treat as a
    retry of the previous POST and skip. This prevents the hook
    retry loop (round 3 hardening) from inflating read counts when
    a server-side commit succeeds but the response is lost.
    """
    data = request.get_json(silent=True) or {}
    skill_id = data.get('skill_id')
    file_path = data.get('file_path')
    session_id = data.get('session_id')
    source = data.get('source') or 'read'
    command_args = data.get('command_args')

    if not _is_non_blank_str(skill_id):
        return jsonify({'ok': False, 'error': 'missing or blank skill_id'}), 400
    if not _is_non_blank_str(file_path):
        return jsonify({'ok': False, 'error': 'missing or blank file_path'}), 400

    from sqlmodel import select as _select
    with SessionLocal() as session:
        if session_id:
            cutoff = (datetime.now()
                      - timedelta(seconds=_helpers._INGEST_DEDUP_WINDOW_SEC)).isoformat()
            existing = session.exec(
                _select(SkillRead.id)
                .where(
                    SkillRead.session_id == session_id,
                    SkillRead.skill_id == skill_id,
                    SkillRead.file_path == file_path,
                    SkillRead.source == source,
                    SkillRead.read_at > cutoff,
                )
                .limit(1)
            ).first()
            if existing is not None:
                return jsonify({'ok': True, 'skipped_duplicate': True})

        session.add(SkillRead(
            skill_id=skill_id, session_id=session_id,
            file_path=file_path, found=int(data.get('found', 1)),
            source=source,
            command_args=command_args,
            read_at=datetime.now().isoformat(),
        ))
        session.commit()
        return jsonify({'ok': True, 'skipped_duplicate': False})


@trace_bp.route('/api/skill-reads')
def api_skill_reads():
    """Keyset-paginated skill-read dashboard.

    Cursor is (read_at DESC, id DESC) so pages stay stable even while the
    hook ingests new reads in the background. `stats` and `sessions`
    summaries are only included on the first page because they describe
    the whole filtered set, not this slice.

    Service-layer extraction: the SQL lives in
    `lib.trace.trace_service.list_skill_reads_page`.
    """
    skill_filter = request.args.get('skill')
    session_filter = request.args.get('session')
    include_tests = request.args.get('include_tests', 'false').lower() in ('1', 'true', 'yes')
    cursor_token = request.args.get('cursor')
    size = clamp_size(request.args.get('size'), default=100)

    page, stats, sessions = trace_service.list_skill_reads_page(
        skill_filter=skill_filter,
        session_filter=session_filter,
        include_tests=include_tests,
        cursor_token=cursor_token,
        size=size,
    )
    envelope = page.to_envelope()
    if cursor_token is None:
        envelope['stats'] = stats
        envelope['sessions'] = sessions
    envelope['skill_filter'] = skill_filter
    envelope['session_filter'] = session_filter
    envelope['include_tests'] = include_tests
    return jsonify(envelope)


@trace_bp.route('/api/skill-reads/reset', methods=['POST'])
def api_reset_skill_reads():
    from sqlalchemy import delete as _delete, func as _func
    from sqlmodel import select as _select
    data = request.get_json(silent=True) or {}
    skill_filter = data.get('skill')
    session_filter = data.get('session')
    with SessionLocal() as session:
        count_stmt = _select(_func.count(SkillRead.id))
        del_stmt = _delete(SkillRead)
        if skill_filter:
            count_stmt = count_stmt.where(SkillRead.skill_id == skill_filter)
            del_stmt = del_stmt.where(SkillRead.skill_id == skill_filter)
        if session_filter:
            count_stmt = count_stmt.where(SkillRead.session_id == session_filter)
            del_stmt = del_stmt.where(SkillRead.session_id == session_filter)
        before = session.exec(count_stmt).one()
        session.execute(del_stmt)
        session.commit()
    return jsonify({'ok': True, 'msg': f'cleared {before} row(s)'})


