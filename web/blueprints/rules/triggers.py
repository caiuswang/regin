"""Rules endpoints split by purpose."""

from __future__ import annotations

from flask import request, jsonify

from lib.auth import require_editor, get_current_user
from lib import audit
from lib.orm.models import PlanSession, RuleTrigger
from lib.utils.pagination import clamp_size

from web.blueprints import rules as _pkg
from web.blueprints.rules import rules_bp
from web.blueprints.rules.trigger_rules import (  # noqa: F401
    _suppression_to_dict, api_triggers_rule_detail, api_triggers_rules,
)


def _active_experiment_ids(events: list) -> dict:
    """Map each distinct non-empty ``guide`` among `events` to the id of the
    active concealment experiment on that pattern (or None when none is
    active). Resolved once per guide so a batch ingest makes one DB lookup
    per distinct guide rather than one per row."""
    from lib import experiments
    out: dict = {}
    for ev in events:
        guide = ev.get('guide') if isinstance(ev, dict) else None
        if guide and guide not in out:
            out[guide] = experiments.get_active_id(guide)
    return out


# ── Rule-trigger log (read + reset + ingest) ───────────────────

@rules_bp.route('/api/triggers')
def api_triggers():
    """Keyset-paginated rule-trigger log.

    The events table grows continuously as the PostToolUse hook fires,
    which rules out offset pagination (concurrent inserts shift every
    page boundary). Cursor is (checked_at DESC, id DESC) so two events
    with identical timestamps can't collide on the page boundary.

    `stats` and `sessions` summaries only travel on the first page
    (`cursor` absent) — they describe the whole filtered set and would
    be identical on every subsequent load-more.
    """
    from sqlalchemy import func as sa_func
    from sqlmodel import select as sm_select
    from lib.orm import SessionLocal
    from lib.orm.models import PlanSession, RuleTrigger
    from lib.utils.pagination import keyset_page_stmt

    rule_filter = request.args.get('rule')
    session_filter = request.args.get('session')
    only_triggered = request.args.get('triggered') == '1'
    cursor_token = request.args.get('cursor')
    size = clamp_size(request.args.get('size'), default=100)

    # Strip the repo-path prefix off file_path for display. Reads the
    # current registered set straight from the DB so removal via /repos
    # takes effect on the next request.
    from lib.orm.models import Repo as _Repo
    with _pkg.SessionLocal() as _root_session:
        _registered_paths = [
            r.path for r in _root_session.exec(sm_select(_Repo)).all() if r.path
        ]
    roots = sorted(
        [p.rstrip('/') + '/' for p in _registered_paths],
        key=len, reverse=True,
    )

    def _trigger_to_dict(row: "RuleTrigger") -> dict:
        fp = row.file_path
        if fp:
            for root in roots:
                if fp.startswith(root):
                    fp = fp[len(root):]
                    break
        return {
            'id': row.id, 'rule_id': row.rule_id, 'file_path': fp,
            'repo': row.repo, 'match_count': row.match_count,
            'triggered': row.triggered, 'severity': row.severity,
            'guide': row.guide, 'summary': row.summary,
            'source': row.source, 'session_id': row.session_id,
            'experiment_id': row.experiment_id, 'checked_at': row.checked_at,
        }

    with _pkg.SessionLocal() as session:
        stmt = sm_select(RuleTrigger)
        if rule_filter:
            stmt = stmt.where(RuleTrigger.rule_id == rule_filter)
        if session_filter:
            stmt = stmt.where(RuleTrigger.session_id == session_filter)
        if only_triggered:
            stmt = stmt.where(RuleTrigger.triggered == 1)

        page = keyset_page_stmt(
            session, stmt,
            order_cols=[(RuleTrigger.checked_at, 'DESC'), (RuleTrigger.id, 'DESC')],
            cursor_token=cursor_token, size=size,
            row_to_dict=_trigger_to_dict,
        )
        envelope = page.to_envelope()

        if cursor_token is None:
            stats_rows = session.exec(
                sm_select(
                    RuleTrigger.rule_id,
                    sa_func.count(RuleTrigger.id).label('total'),
                    sa_func.coalesce(sa_func.sum(RuleTrigger.triggered), 0).label('fired'),
                    sa_func.max(RuleTrigger.checked_at).label('last_seen'),
                ).group_by(RuleTrigger.rule_id)
                 .order_by(sa_func.max(RuleTrigger.checked_at).desc())
            ).all()
            envelope['stats'] = [dict(r._mapping) for r in stats_rows]

            # Correlated subquery: latest plan_filename per session_id.
            plan_subq = (
                sm_select(PlanSession.plan_filename)
                .where(PlanSession.session_id == RuleTrigger.session_id)
                .order_by(PlanSession.started_at.desc())
                .limit(1)
                .scalar_subquery()
            )
            session_id_expr = sa_func.coalesce(RuleTrigger.session_id, '').label('session_id')
            sessions_rows = session.exec(
                sm_select(
                    session_id_expr,
                    sa_func.count(RuleTrigger.id).label('total'),
                    sa_func.coalesce(sa_func.sum(RuleTrigger.triggered), 0).label('fired'),
                    sa_func.count(sa_func.distinct(RuleTrigger.rule_id)).label('rules'),
                    sa_func.count(sa_func.distinct(RuleTrigger.file_path)).label('files'),
                    sa_func.min(RuleTrigger.checked_at).label('first_seen'),
                    sa_func.max(RuleTrigger.checked_at).label('last_seen'),
                    plan_subq.label('plan_filename'),
                )
                .group_by(sa_func.coalesce(RuleTrigger.session_id, ''))
                .order_by(sa_func.max(RuleTrigger.checked_at).desc())
                .limit(50)
            ).all()
            envelope['sessions'] = [dict(r._mapping) for r in sessions_rows]

    envelope['rule_filter'] = rule_filter
    envelope['session_filter'] = session_filter
    envelope['only_triggered'] = only_triggered
    return jsonify(envelope)


from lib.auth import require_role


def _reset_cutoff_iso(older_than_days) -> tuple[str | None, str | None]:
    """`(cutoff_iso, error)` for the reset retention window; `None` or 0
    days means no age cutoff."""
    from datetime import datetime, timedelta, timezone
    if older_than_days is None:
        return None, None
    try:
        n = int(older_than_days)
    except (TypeError, ValueError):
        return None, 'older_than_days must be an integer'
    if n < 0:
        return None, 'older_than_days must be non-negative'
    if n == 0:
        return None, None
    cutoff = datetime.now(timezone.utc) - timedelta(days=n)
    return cutoff.strftime('%Y-%m-%d %H:%M:%S'), None


def _delete_triggers_scoped(rule_filter, session_filter, cutoff_iso) -> int:
    """Delete the matching rule_triggers rows; returns how many matched."""
    from sqlalchemy import delete, func as sa_func
    from sqlmodel import select as sm_select

    clauses = []
    if rule_filter:
        clauses.append(RuleTrigger.rule_id == rule_filter)
    if session_filter:
        clauses.append(RuleTrigger.session_id == session_filter)
    if cutoff_iso:
        clauses.append(RuleTrigger.checked_at < cutoff_iso)
    with _pkg.SessionLocal() as session:
        before = session.exec(
            sm_select(sa_func.count(RuleTrigger.id)).where(*clauses)).one()
        session.execute(delete(RuleTrigger).where(*clauses))
        session.commit()
    return before


def _reset_scope_label(rule_filter, session_filter, cutoff_iso,
                       older_than_days) -> str:
    scope_bits: list[str] = []
    if rule_filter:    scope_bits.append(f"rule={rule_filter}")
    if session_filter: scope_bits.append(f"session={session_filter}")
    if cutoff_iso:     scope_bits.append(f"older_than_days={older_than_days}")
    return ', '.join(scope_bits) or 'all'


@rules_bp.route('/api/triggers/reset', methods=['POST'])
@require_role('admin')
def api_reset_triggers():
    """Admin-only: wipe rule_triggers, optionally scoped.

    Body fields (all optional):
      - `rule`             — restrict to one rule_id
      - `session`          — restrict to one session_id
      - `older_than_days`  — retention policy: delete only rows whose
                             `checked_at` is older than now - N days.
                             `null` or 0 means "no age cutoff" (wipe
                             everything that matches the other scopes).

    Combinable: e.g. `{rule: "X", older_than_days: 30}` deletes events
    for rule X older than 30 days. Suppressed rows are NOT exempt —
    the suppression metadata gets cleaned by ON DELETE cascade.
    """
    data = request.get_json(silent=True) or {}
    rule_filter = data.get('rule')
    session_filter = data.get('session')
    older_than_days = data.get('older_than_days')
    cutoff_iso, error = _reset_cutoff_iso(older_than_days)
    if error:
        return jsonify({'ok': False, 'error': error}), 400

    before = _delete_triggers_scoped(rule_filter, session_filter, cutoff_iso)

    from lib import audit
    from lib.auth import get_current_user
    user = get_current_user() or {}
    scope = _reset_scope_label(rule_filter, session_filter, cutoff_iso,
                               older_than_days)
    audit.log_action(
        user.get('id'), user.get('username') or 'unknown',
        action='reset_triggers', target=scope,
        detail={'rows_deleted': int(before), 'cutoff_iso': cutoff_iso},
    )
    return jsonify({'ok': True, 'msg': f'cleared {before} row(s)'})


def _rule_trigger_event_error(ev) -> str | None:
    # Validators live in web.helpers since Phase 8 of the web refactor.
    # Import from there directly rather than via web.app's re-export —
    # matches the pattern every other ingest endpoint follows.
    from web.helpers import _is_non_blank_str
    if not isinstance(ev, dict):
        return 'event must be an object'
    if not _is_non_blank_str(ev.get('rule_id')):
        return 'missing or blank rule_id'
    if not _is_non_blank_str(ev.get('file_path')):
        return 'missing or blank file_path'
    return None


def _insert_rule_triggers(data, exp_by_guide) -> None:
    with _pkg.SessionLocal() as session:
        for ev in data:
            match_count = int(ev.get('match_count') or 0)
            session.add(RuleTrigger(
                rule_id=ev.get('rule_id'),
                file_path=ev.get('file_path'),
                repo=ev.get('repo'),
                match_count=match_count,
                triggered=1 if match_count > 0 else 0,
                severity=ev.get('severity'),
                guide=ev.get('guide'),
                summary=ev.get('summary'),
                source=ev.get('source'),
                session_id=ev.get('session_id'),
                span_id=ev.get('span_id'),
                experiment_id=exp_by_guide.get(ev.get('guide')),
            ))
        session.commit()


@rules_bp.route('/api/rule-triggers', methods=['POST'])
def api_ingest_rule_trigger():
    """Ingest one or more GritQL rule-check events.

    Body: a single event dict or a list. Required per event:
    `rule_id` (non-blank) and `file_path` (non-blank) — these are
    the only two fields anything else joins back on; an empty
    string would create an orphan row invisible to every UI view.

    The whole batch is wrapped in a single transaction. Any invalid
    event aborts validation with 400 and ZERO rows written.
    Oversized batches (>`_ingest_max_batch_size()`) return 413 —
    a runaway hook that fires on every keystroke won't fill memory.
    """
    from web.helpers import _ingest_max_batch_size

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({'ok': False, 'error': 'invalid JSON body'}), 400
    if not isinstance(data, list):
        data = [data]

    max_batch = _ingest_max_batch_size()
    if len(data) > max_batch:
        return jsonify({
            'ok': False,
            'error': f'batch too large: {len(data)} events (max: {max_batch})',
        }), 413

    errors = [{'index': i, 'reason': reason}
              for i, reason in enumerate(map(_rule_trigger_event_error, data))
              if reason]
    if errors:
        return jsonify({'ok': False, 'ingested': 0, 'errors': errors}), 400

    # Stamp each event with the active concealment experiment (if any) for the
    # rule's guide, so the experiment-vs-baseline rollup can attribute fires.
    # A guide maps 1:1 to a pattern slug; resolve once per distinct guide so a
    # batch doesn't hit the DB per row. A None value is the baseline bucket.
    exp_by_guide = _active_experiment_ids(data)

    try:
        _insert_rule_triggers(data, exp_by_guide)
    except Exception as exc:
        return jsonify({
            'ok': False,
            'ingested': 0,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({'ok': True, 'ingested': len(data)})


@rules_bp.route('/api/settings/rule-triggers/thresholds')
def api_rule_trigger_thresholds():
    """Read the threshold block. Both list/drawer endpoints already echo
    these in their responses; this endpoint backs the standalone
    Settings card so it doesn't have to ping /api/triggers/rules just
    to render the form."""
    from lib.settings import settings as _settings
    return jsonify(_settings.rule_trigger_thresholds.model_dump())


def _threshold_int(body: dict, key: str, default: int, is_valid,
                   bound_msg: str, errors: list) -> int | None:
    try:
        value = int(body.get(key, default))
    except (TypeError, ValueError):
        errors.append(f'{key} must be an integer')
        return None
    if not is_valid(value):
        errors.append(bound_msg)
    return value


@rules_bp.route('/api/settings/rule-triggers/thresholds', methods=['PUT'])
@require_role('admin')
def api_update_rule_trigger_thresholds():
    """Admin-only write. Persists to settings.local.json and reloads the
    in-process settings singleton so subsequent /api/triggers/rules
    queries see the new thresholds without a server restart.
    """
    from lib.rules import buckets as _buckets
    from lib.settings import RuleTriggerThresholds

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'ok': False, 'error': 'invalid JSON body'}), 400

    # Validate before persisting — bad input shouldn't pollute the file.
    errors = []
    rate = _threshold_int(body, 'noisy_min_rate_pct', 30, lambda v: 0 <= v <= 100,
                          'noisy_min_rate_pct must be 0–100', errors)
    fires = _threshold_int(body, 'noisy_min_fires', 5, lambda v: v >= 0,
                           'noisy_min_fires must be non-negative', errors)
    checks = _threshold_int(body, 'dead_min_checks', 3, lambda v: v >= 1,
                            'dead_min_checks must be at least 1', errors)
    rng = body.get('default_range', '7d')
    if rng not in _buckets.VALID_RANGES:
        errors.append(
            f"default_range must be one of {_buckets.VALID_RANGES}"
        )
    if errors:
        return jsonify({'ok': False, 'errors': errors}), 400

    # Re-validate via pydantic for consistency with the typed Settings.
    block = RuleTriggerThresholds(
        noisy_min_rate_pct=rate,
        noisy_min_fires=fires,
        dead_min_checks=checks,
        default_range=rng,
    )

    from lib.settings import save_settings
    save_settings(
        {'rule_trigger_thresholds': block.model_dump()},
        scope='shared',
    )

    from lib import audit
    from lib.auth import get_current_user
    user = get_current_user() or {}
    audit.log_action(
        user.get('id'), user.get('username') or 'unknown',
        action='update_rule_trigger_thresholds', target='settings',
        detail=block.model_dump(),
    )
    return jsonify({'ok': True, 'thresholds': block.model_dump()})


@rules_bp.route('/api/triggers/by-span/<span_id>')
def api_triggers_by_span(span_id: str):
    """Look up rule_trigger rows tied to one PostToolUse span."""
    from sqlmodel import select as sm_select
    from lib.orm.models import RuleTriggerSuppression

    with _pkg.SessionLocal() as session:
        rows = session.exec(
            sm_select(RuleTrigger)
            .where(RuleTrigger.span_id == span_id)
            .order_by(RuleTrigger.rule_id)
        ).all()
        if not rows:
            return jsonify({'triggers': []})

        ids = [r.id for r in rows]
        suppr = {
            s.rule_trigger_id: _suppression_to_dict(s)
            for s in session.exec(
                sm_select(RuleTriggerSuppression)
                .where(RuleTriggerSuppression.rule_trigger_id.in_(ids))
            ).all()
        }

    return jsonify({
        'triggers': [
            {
                'id': r.id,
                'rule_id': r.rule_id,
                'file_path': r.file_path,
                'checked_at': r.checked_at,
                'match_count': r.match_count,
                'triggered': r.triggered,
                'suppressed': bool(r.suppressed),
                'suppression': suppr.get(r.id),
            }
            for r in rows
        ],
    })


# ── Per-event suppression (PR-4) ───────────────────────────

def _suppress_reason(body: dict) -> str | None:
    reason = body.get('reason')
    if reason is None:
        return None
    return str(reason).strip() or None


def _refresh_suppression_reason(session, existing, reason) -> bool:
    """Already suppressed. Update the reason if the caller passed a new one —
    otherwise the second click silently drops the user's text. Leaves
    who/when intact to preserve the original authorship."""
    if reason is None or reason == existing.reason:
        return False
    existing.reason = reason
    session.add(existing)
    session.commit()
    session.refresh(existing)
    return True


def _insert_suppression(session, trigger_id: int, user: dict, reason):
    from sqlalchemy import update as sa_update
    from lib.orm.models import RuleTriggerSuppression

    row = RuleTriggerSuppression(
        rule_trigger_id=trigger_id,
        suppressed_by_id=user.get('id') or 0,
        suppressed_by_username=user.get('username') or 'unknown',
        reason=reason,
    )
    session.add(row)
    session.execute(
        sa_update(RuleTrigger)
        .where(RuleTrigger.id == trigger_id)
        .values(suppressed=1)
    )
    session.commit()
    session.refresh(row)
    return row


@rules_bp.route('/api/triggers/<int:trigger_id>/suppress', methods=['POST'])
@require_editor
def api_suppress_trigger(trigger_id: int):
    """Mark one rule_trigger event as noise.

    Editor+ role. Idempotent: re-POSTing returns the existing
    suppression row instead of erroring on the UNIQUE constraint.
    The `rule_triggers.suppressed` boolean is flipped in the same
    transaction so the hot-path aggregate filter stays consistent
    with the metadata row.
    """
    from sqlmodel import select as sm_select
    from lib.orm.models import RuleTrigger, RuleTriggerSuppression
    from lib import audit
    from lib.auth import get_current_user

    user = get_current_user() or {}
    reason = _suppress_reason(request.get_json(silent=True) or {})

    with _pkg.SessionLocal() as session:
        rt = session.get(RuleTrigger, trigger_id)
        if rt is None:
            return jsonify({'ok': False, 'error': 'rule_trigger not found'}), 404

        existing = session.exec(
            sm_select(RuleTriggerSuppression)
            .where(RuleTriggerSuppression.rule_trigger_id == trigger_id)
        ).first()
        if existing is not None:
            updated = _refresh_suppression_reason(session, existing, reason)
            return jsonify({
                'ok': True, 'idempotent': True, 'reason_updated': updated,
                'suppression': _suppression_to_dict(existing),
            })
        row = _insert_suppression(session, trigger_id, user, reason)

    audit.log_action(
        user.get('id'), user.get('username') or 'unknown',
        action='suppress_rule_trigger', target=str(trigger_id),
        detail={'rule_id': rt.rule_id, 'reason': reason},
    )
    return jsonify({'ok': True, 'suppression': _suppression_to_dict(row)})


@rules_bp.route('/api/triggers/<int:trigger_id>/suppress', methods=['DELETE'])
@require_editor
def api_unsuppress_trigger(trigger_id: int):
    """Un-mark a previously-suppressed event. Editor+ role."""
    from sqlalchemy import delete as sa_delete, update as sa_update
    from sqlmodel import select as sm_select
    from lib.orm.models import RuleTrigger, RuleTriggerSuppression
    from lib import audit
    from lib.auth import get_current_user

    user = get_current_user() or {}

    with _pkg.SessionLocal() as session:
        rt = session.get(RuleTrigger, trigger_id)
        if rt is None:
            return jsonify({'ok': False, 'error': 'rule_trigger not found'}), 404

        existing = session.exec(
            sm_select(RuleTriggerSuppression)
            .where(RuleTriggerSuppression.rule_trigger_id == trigger_id)
        ).first()
        if existing is None:
            # Idempotent — already un-suppressed.
            return jsonify({'ok': True, 'idempotent': True})

        session.execute(
            sa_delete(RuleTriggerSuppression)
            .where(RuleTriggerSuppression.rule_trigger_id == trigger_id)
        )
        session.execute(
            sa_update(RuleTrigger)
            .where(RuleTrigger.id == trigger_id)
            .values(suppressed=0)
        )
        session.commit()

    audit.log_action(
        user.get('id'), user.get('username') or 'unknown',
        action='unsuppress_rule_trigger', target=str(trigger_id),
        detail={'rule_id': rt.rule_id},
    )
    return jsonify({'ok': True})


RETENTION_WINDOWS_DAYS = (7, 30, 90, 365)


@rules_bp.route('/api/triggers/stats')
def api_triggers_stats():
    """Total + oldest + per-age counts. Backs the Settings retention card.

    `older_than[N]` counts let the UI preview how many rows each retention
    policy would delete *before* the admin clicks Reset — so picking
    "older than 30 days" shows a row count instead of just a label.
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import func as sa_func
    from sqlmodel import select as sm_select
    from lib.orm.models import RuleTrigger

    now = datetime.now(timezone.utc)
    with _pkg.SessionLocal() as session:
        total = session.exec(sm_select(sa_func.count(RuleTrigger.id))).one()
        oldest = session.exec(sm_select(sa_func.min(RuleTrigger.checked_at))).one()
        distinct_rules = session.exec(
            sm_select(sa_func.count(sa_func.distinct(RuleTrigger.rule_id)))
        ).one()
        older_than: dict[int, int] = {}
        for days in RETENTION_WINDOWS_DAYS:
            cutoff = (now - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
            n = session.exec(
                sm_select(sa_func.count(RuleTrigger.id))
                .where(RuleTrigger.checked_at < cutoff)
            ).one()
            older_than[days] = int(n or 0)

    return jsonify({
        'total': int(total or 0),
        'oldest_at': oldest,
        'distinct_rules': int(distinct_rules or 0),
        'older_than': older_than,  # {7: N, 30: N, 90: N, 365: N}
    })
