"""Session mutations: delete, tags, close, materialize, repair and features."""

from __future__ import annotations

from datetime import datetime, timezone
from flask import request, jsonify
from lib.trace.session_tags import is_builtin, normalize_custom_slug
from lib.orm import SessionLocal
from lib.orm.models import (
    PlanSession, PromptImage, RuleTrigger, Session as SessionModel,
    SessionRepo, SessionSpan, SessionTag, SessionTraceMap, SkillRead,
    TurnUsage,
)
from lib.trace import trace_service
from web.blueprints.trace import trace_bp
from web.blueprints.trace.sessions.common import (
    _alias_group_ids, _expand_alias_groups,
)


# Every table that carries a session's id, paired with the column it lives
# under and the response key it reports. The single- and batch-delete paths
# both run this list so they can never drift (a missed table here leaks rows
# that no reader can ever reach again — they go stale forever). Keep in sync
# with db/schema.sql whenever a new trace_id/session_id-keyed table is added.
_SESSION_DELETE_TARGETS = (
    ('sessions', SessionModel, SessionModel.trace_id),
    ('spans', SessionSpan, SessionSpan.trace_id),
    ('trace_map', SessionTraceMap, SessionTraceMap.trace_id),
    ('turn_usage', TurnUsage, TurnUsage.trace_id),
    ('session_repos', SessionRepo, SessionRepo.trace_id),
    ('session_tags', SessionTag, SessionTag.trace_id),
    ('skill_reads', SkillRead, SkillRead.session_id),
    ('plan_sessions', PlanSession, PlanSession.session_id),
    ('rule_triggers', RuleTrigger, RuleTrigger.session_id),
    ('prompt_images', PromptImage, PromptImage.trace_id),
)


def _delete_session_rows(session, trace_id):
    """Delete every row keyed to `trace_id` across all session tables.

    Returns a dict of per-table rowcounts. Caller owns the transaction so
    single- and batch-delete can each wrap their own BEGIN IMMEDIATE.
    """
    from sqlalchemy import delete as _delete
    counts = {}
    for key, model, column in _SESSION_DELETE_TARGETS:
        counts[key] = session.execute(
            _delete(model).where(column == trace_id)
        ).rowcount
    return counts


def _batch_trace_ids_error(trace_ids) -> str | None:
    if not isinstance(trace_ids, list) or not all(isinstance(t, str) and t for t in trace_ids):
        return 'trace_ids must be a non-empty list of strings'
    if not trace_ids:
        return 'trace_ids must not be empty'
    return None


def _delete_session_batch(trace_ids) -> dict:
    totals = {key: 0 for key, _, _ in _SESSION_DELETE_TARGETS}
    with SessionLocal() as session:
        # A regin-launched run is two trace ids shown as one row, so
        # deleting the row the user sees has to take both halves. Removing
        # only the canonical one left the other holding the session's
        # spans, and it surfaced as a NEW row where the deleted one had
        # been — the same session, apparently undeletable.
        for trace_id in _expand_alias_groups(trace_ids):
            for key, count in _delete_session_rows(session, trace_id).items():
                totals[key] += count
        session.commit()
    return totals


@trace_bp.route('/api/sessions/batch-delete', methods=['POST'])
def api_session_batch_delete():
    """Delete multiple sessions in a single transaction.

    Body: `{"trace_ids": ["id1", "id2", ...]}`

    Wrapped in BEGIN IMMEDIATE so a crash midway through a large batch
    cannot leave some sessions partially deleted. Unknown trace_ids are
    simply no-ops (rowcount==0) instead of an error, so the caller can
    retry a batch idempotently after a timeout.
    """
    body = request.get_json(silent=True) or {}
    trace_ids = body.get('trace_ids') or []
    error = _batch_trace_ids_error(trace_ids)
    if error:
        return jsonify({'ok': False, 'error': error}), 400

    try:
        totals = _delete_session_batch(trace_ids)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({
        'ok': True,
        'processed': len(trace_ids),
        'deleted': totals,
    })


@trace_bp.route('/api/sessions/<trace_id>', methods=['DELETE'])
def api_session_delete(trace_id):
    """Delete a session and all of its related trace rows.

    Every session-keyed table is cleared (see `_SESSION_DELETE_TARGETS`):
    sessions/session_spans/session_trace_map/turn_usage/prompt_images,
    session_repos and session_tags key on trace_id; skill_reads/
    plan_sessions/rule_triggers key on session_id. Everything is removed in
    one BEGIN IMMEDIATE so a
    crash mid-delete can't leave half a session in the DB (which would leak
    into the sessions list with a missing title or zero spans) or strand
    trace_map/turn_usage/repo rows that no reader can ever reach again.
    """
    try:
        with SessionLocal() as session:
            deleted = {key: 0 for key, _, _ in _SESSION_DELETE_TARGETS}
            # Both halves of a regin-launched run — see the batch endpoint.
            for member in _alias_group_ids(trace_id):
                for key, count in _delete_session_rows(session, member).items():
                    deleted[key] += count
            session.commit()
    except Exception as exc:
        return jsonify({
            'ok': False,
            'trace_id': trace_id,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({
        'ok': True,
        'trace_id': trace_id,
        'deleted': deleted,
    })


def _session_custom_tags(session, trace_id):
    """The custom tags on a session as `{slug, source}`, sorted by slug.

    Carries `source` ('manual' | 'auto') so a mutation response lets the
    frontend re-render the row's chips without relabeling auto tags as manual.
    """
    from sqlmodel import select as _select
    rows = session.exec(
        _select(SessionTag.tag, SessionTag.source)
        .where(SessionTag.trace_id == trace_id)
    ).all()
    return [{'slug': tag, 'source': source or 'manual'}
            for tag, source in sorted(rows)]


@trace_bp.route('/api/session-tags')
def api_session_tags():
    """List the custom tags in use, with how many sessions carry each.

    Powers the tag facet's custom section and add-tag autocomplete. Builtin
    category tags are NOT listed here — they come from `builtin_tags` /
    `tag_counts` on the sessions envelope (they're derived, not stored).
    """
    from sqlalchemy import func as _func
    from sqlmodel import select as _select
    with SessionLocal() as session:
        rows = session.exec(
            _select(SessionTag.tag, _func.count())
            .group_by(SessionTag.tag)
            .order_by(SessionTag.tag)
        ).all()
    return jsonify({'tags': [{'slug': t, 'count': n} for t, n in rows]})


@trace_bp.route('/api/sessions/<trace_id>/tags', methods=['POST'])
def api_session_tag_add(trace_id):
    """Add a custom tag to a session (idempotent).

    Body: `{"tag": "<slug>"}`. The slug is normalized/validated; builtin
    category slugs are rejected (they're intrinsic, derived from origin, and
    can't be hand-assigned). Returns the session's full custom tag list.
    """
    body = request.get_json(silent=True) or {}
    slug = normalize_custom_slug(body.get('tag'))
    if not slug:
        return jsonify({
            'ok': False,
            'error': 'tag must be a slug of a-z, 0-9, dash (1-40 chars) and '
                     'not a builtin category',
        }), 400
    try:
        with SessionLocal() as session:
            exists = session.get(SessionTag, (trace_id, slug))
            if exists is None:
                now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                session.add(SessionTag(trace_id=trace_id, tag=slug,
                                       source='manual', created_at=now))
                session.commit()
            tags = _session_custom_tags(session, trace_id)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({'ok': True, 'trace_id': trace_id, 'tags': tags})


@trace_bp.route('/api/sessions/<trace_id>/tags/<tag>', methods=['DELETE'])
def api_session_tag_remove(trace_id, tag):
    """Remove a custom tag from a session.

    Builtin category slugs can't be removed (they're derived, not stored);
    the attempt is a 400 rather than a silent no-op. Unknown (session, tag)
    pairs delete zero rows and still return ok — idempotent.
    """
    from sqlalchemy import delete as _delete
    if is_builtin(tag):
        return jsonify({
            'ok': False,
            'error': f'"{tag}" is a builtin category and cannot be removed',
        }), 400
    try:
        with SessionLocal() as session:
            session.execute(
                _delete(SessionTag)
                .where(SessionTag.trace_id == trace_id)
                .where(SessionTag.tag == tag)
            )
            session.commit()
            tags = _session_custom_tags(session, trace_id)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({'ok': True, 'trace_id': trace_id, 'tags': tags})


@trace_bp.route('/api/sessions/<trace_id>/close', methods=['POST'])
def api_session_close(trace_id):
    """Manually mark a session as ended.

    Some sessions crash or get interrupted before a SessionEnd span ever
    fires, so their status stays 'active' (or NULL) with no way to settle
    it from the trace itself. This records a manual close: status='ended',
    ended_reason='manual', and — when no natural end exists yet — ended_at
    set to now.

    Setting ended_at is what makes the close *stick*: the sessions upsert
    (`_SESSIONS_UPSERT_SQL` in trace_service.ingest) recomputes status on
    every re-ingested span batch from `ended_at` vs `last_start_at`, so a
    status-only write would flip back to 'active' the next time a late span
    lands. A now() ended_at is >= any past start, so the CASE resolves to
    'ended' and holds. An existing (natural) ended_at is left untouched.
    """
    try:
        with SessionLocal() as session:
            row = session.get(SessionModel, trace_id)
            if row is None:
                return jsonify({
                    'ok': False,
                    'trace_id': trace_id,
                    'error': 'session not found',
                }), 404
            row.status = 'ended'
            row.ended_reason = 'manual'
            if row.ended_at is None:
                row.ended_at = datetime.now().isoformat()
            session.commit()
            ended_at = row.ended_at
    except Exception as exc:
        return jsonify({
            'ok': False,
            'trace_id': trace_id,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({
        'ok': True,
        'trace_id': trace_id,
        'status': 'ended',
        'ended_reason': 'manual',
        'ended_at': ended_at,
    })


@trace_bp.route('/api/sessions/<trace_id>/materialize', methods=['POST'])
def api_session_materialize(trace_id):
    """Persist the orphan-graft + envelope-widen projection back to the DB.

    Wraps the fetch + projection + persist chain in BEGIN IMMEDIATE so:
    • Concurrent ingest waits for the projection to commit instead of
      slipping rows in between fetch and writes.
    • Multiple UPDATEs either all apply or none do.

    Exposed separately from GET so the read path stays safe/idempotent.
    Service: `lib.trace.trace_service.materialize_session`.
    """
    try:
        updated = trace_service.materialize_session(trace_id)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'trace_id': trace_id,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({
        'ok': True,
        'trace_id': trace_id,
        'parent_updates': updated['parent_updates'],
        'envelope_updates': updated['envelope_updates'],
    })


@trace_bp.route('/api/sessions/<trace_id>/repair-spans', methods=['POST'])
def api_session_repair_spans(trace_id):
    """Recover assistant_response / assistant.thinking / harness.* spans
    that turn_trace's seen-uuid cache locked out.

    See `lib.trace.repair.repair_session_spans` for the full mechanism.
    Idempotent: re-running on a healed session is a no-op because the
    cache no longer holds any uuids without spans.
    """
    from lib.trace.repair import repair_session_spans

    try:
        result = repair_session_spans(trace_id)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'trace_id': trace_id,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    status = 200 if result.get('ok') else 404
    return jsonify(result), status






@trace_bp.route('/api/sessions/<trace_id>/features')
def api_session_features(trace_id):
    """What this session built, read the way the feature page counts it.

    The merged reader, not `features_for_session`: that one answers what the
    session *claimed*, which is what blame demands, and on the live database
    22 of 23 attributed traces reach their feature only through the task lane.
    A header built on the narrower reader would call them unattributed while
    the feature page counted them — two surfaces disagreeing about one fact.

    It lives on the trace blueprint because it is a property of a session, and
    the trace surface's admin gate is what governs it.
    """
    from lib.features import attribution  # noqa: PLC0415  (heavy import)
    from lib.features import store as feature_store  # noqa: PLC0415
    rows = attribution.attributions_for_session(trace_id)
    # The title rides along resolved, and it is `features.title` — the
    # authority for what a feature is called. No surface falls back to the
    # title of whichever task happens to sit under it.
    titles = {f.id: f.title for f in feature_store.population()}
    return jsonify({
        "trace_id": trace_id,
        "features": [{"feature_id": row.feature_id,
                      "title": titles.get(row.feature_id),
                      "provenance": row.provenance,
                      "is_primary": bool(row.is_primary),
                      "round": row.round,
                      "task_id": row.task_id} for row in rows],
    })
