"""Rules endpoints split by purpose."""

from __future__ import annotations

import fnmatch as _fnmatch
import os
import re as _re
import subprocess
from dataclasses import dataclass

from flask import request, jsonify

from lib.auth import require_editor, get_current_user
from lib import audit, rule_engines
from lib.rules import grit_rule_index
from lib.orm import SessionLocal
from lib.orm.models import PlanSession, RuleTrigger
from lib.utils.pagination import clamp_size

from web.blueprints import rules as _pkg
from web.blueprints.rules import rules_bp
from web.blueprints.rules._helpers import (
    _engine_rule_to_dict, _all_rules_index, _engine_descriptor,
    _rule_capabilities, _decorate_rule,
)


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
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete, func as sa_func
    from sqlmodel import select as sm_select
    from lib.orm.models import RuleTrigger

    data = request.get_json(silent=True) or {}
    rule_filter = data.get('rule')
    session_filter = data.get('session')
    older_than_days = data.get('older_than_days')
    cutoff_iso: str | None = None
    if older_than_days is not None:
        try:
            n = int(older_than_days)
        except (TypeError, ValueError):
            return jsonify({
                'ok': False, 'error': 'older_than_days must be an integer',
            }), 400
        if n < 0:
            return jsonify({
                'ok': False, 'error': 'older_than_days must be non-negative',
            }), 400
        if n > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=n)
            cutoff_iso = cutoff.strftime('%Y-%m-%d %H:%M:%S')

    with _pkg.SessionLocal() as session:
        count_stmt = sm_select(sa_func.count(RuleTrigger.id))
        del_stmt = delete(RuleTrigger)
        if rule_filter:
            count_stmt = count_stmt.where(RuleTrigger.rule_id == rule_filter)
            del_stmt = del_stmt.where(RuleTrigger.rule_id == rule_filter)
        if session_filter:
            count_stmt = count_stmt.where(RuleTrigger.session_id == session_filter)
            del_stmt = del_stmt.where(RuleTrigger.session_id == session_filter)
        if cutoff_iso:
            count_stmt = count_stmt.where(RuleTrigger.checked_at < cutoff_iso)
            del_stmt = del_stmt.where(RuleTrigger.checked_at < cutoff_iso)
        before = session.exec(count_stmt).one()
        session.execute(del_stmt)
        session.commit()

    from lib import audit
    from lib.auth import get_current_user
    user = get_current_user() or {}
    scope_bits: list[str] = []
    if rule_filter:    scope_bits.append(f"rule={rule_filter}")
    if session_filter: scope_bits.append(f"session={session_filter}")
    if cutoff_iso:     scope_bits.append(f"older_than_days={older_than_days}")
    scope = ', '.join(scope_bits) or 'all'
    audit.log_action(
        user.get('id'), user.get('username') or 'unknown',
        action='reset_triggers', target=scope,
        detail={'rows_deleted': int(before), 'cutoff_iso': cutoff_iso},
    )
    return jsonify({'ok': True, 'msg': f'cleared {before} row(s)'})


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
    # Validators live in web.helpers since Phase 8 of the web refactor.
    # Import from there directly rather than via web.app's re-export —
    # matches the pattern every other ingest endpoint follows.
    from web.helpers import _is_non_blank_str, _ingest_max_batch_size

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

    errors = []
    for i, ev in enumerate(data):
        if not isinstance(ev, dict):
            errors.append({'index': i, 'reason': 'event must be an object'})
            continue
        if not _is_non_blank_str(ev.get('rule_id')):
            errors.append({'index': i, 'reason': 'missing or blank rule_id'})
            continue
        if not _is_non_blank_str(ev.get('file_path')):
            errors.append({'index': i, 'reason': 'missing or blank file_path'})
            continue
    if errors:
        return jsonify({'ok': False, 'ingested': 0, 'errors': errors}), 400

    try:
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
                ))
            session.commit()
    except Exception as exc:
        return jsonify({
            'ok': False,
            'ingested': 0,
            'error': f'{type(exc).__name__}: {exc}',
        }), 500
    return jsonify({'ok': True, 'ingested': len(data)})


# ── Per-rule operator view (Option B Rule-Triggers redesign) ───────────────
#
# The /trace/triggers UI lists one row per rule, with health classified by
# the user-configurable thresholds in `settings.rule_trigger_thresholds`.
# Heavy detail (full guide text, full files/sessions tables, recent events)
# is fetched lazily by the drawer endpoint below.


def _all_configured_rules() -> dict:
    """rule_id → Rule across every configured engine.

    Returned shape: {rid: lib.rule_engines.base.Rule}. Engines that fail to
    parse contribute nothing; the per-rule UI still shows the historical
    rows from `rule_triggers` for that id.
    """
    from lib import rule_engines as _engines
    result: dict = {}
    for engine in _engines.all_engines():
        try:
            for rule in engine.parse_rules():
                result[rule.id] = rule
        except Exception:
            # An engine that can't parse shouldn't 500 the page.
            continue
    return result


def _truncate(text: str, n: int = 140) -> str:
    text = (text or '').strip()
    if len(text) <= n:
        return text
    return text[:n].rstrip() + '…'


@dataclass(frozen=True)
class _TriggersRulesParams:
    range_str: str
    search: str
    sev_filter: set
    eng_filter: set
    status_filter: str
    marks_only: bool
    sort_by: str


def _parse_triggers_rules_params(request_obj, default_range: str) -> _TriggersRulesParams:
    """Pull + normalise the query-string knobs the rules dashboard accepts."""
    return _TriggersRulesParams(
        range_str=(request_obj.args.get('range') or default_range).lower(),
        search=(request_obj.args.get('search') or '').strip().lower(),
        sev_filter={s for s in (request_obj.args.get('severity') or '').split(',') if s},
        eng_filter={s for s in (request_obj.args.get('engine') or '').split(',') if s},
        status_filter=(request_obj.args.get('status') or 'all').lower(),
        # "marks=1" → only rules with at least one suppressed event in the
        # active window. Separate from the status classification because
        # "noisy" (high trigger rate) and "user-flagged as noise" are
        # different signals — same word, different semantics.
        marks_only=request_obj.args.get('marks') == '1',
        sort_by=(request_obj.args.get('sort') or 'rate').lower(),
    )


def _query_trigger_aggregates(session, window_start: str, bucket_pattern: str) -> dict:
    """Run the 6 GROUP BY queries that feed the rule dashboard.

    Returns a dict with keys agg / suppr_by_rule / spark_by_rule /
    exp_by_rule / source_by_rule / files_by_rule. All queries scope to
    `checked_at >= window_start`; suppressed rows drop out of every
    KPI-bearing aggregate except suppr_by_rule itself.
    """
    from collections import defaultdict
    import os
    from sqlalchemy import func as sa_func
    from sqlmodel import select as sm_select

    # Aggregate: fires/checks/last_seen per rule within window.
    # Suppressed events drop out of fires AND checks (the user
    # flagged them as noise; they shouldn't move any KPI).
    agg_rows = session.exec(
        sm_select(
            RuleTrigger.rule_id,
            sa_func.count(RuleTrigger.id).label('checks'),
            sa_func.coalesce(sa_func.sum(RuleTrigger.triggered), 0).label('fires'),
            sa_func.max(RuleTrigger.checked_at).label('last_seen'),
        )
        .where(RuleTrigger.checked_at >= window_start)
        .where(RuleTrigger.suppressed == 0)
        .group_by(RuleTrigger.rule_id)
    ).all()
    agg = {r.rule_id: r for r in agg_rows}

    # Suppressed counts per rule (for the "(N suppressed)" hint).
    # Same window, only the rows the user flagged.
    suppr_rows = session.exec(
        sm_select(
            RuleTrigger.rule_id,
            sa_func.count(RuleTrigger.id).label('n'),
        )
        .where(RuleTrigger.checked_at >= window_start)
        .where(RuleTrigger.suppressed == 1)
        .group_by(RuleTrigger.rule_id)
    ).all()
    suppr_by_rule = {r.rule_id: int(r.n) for r in suppr_rows}

    # Spark: one query, GROUP BY rule_id + bucket. Zero-filled per row.
    spark_rows = session.exec(
        sm_select(
            RuleTrigger.rule_id,
            sa_func.strftime(bucket_pattern, RuleTrigger.checked_at).label('bucket'),
            sa_func.coalesce(sa_func.sum(RuleTrigger.triggered), 0).label('fires'),
        )
        .where(RuleTrigger.checked_at >= window_start)
        .where(RuleTrigger.suppressed == 0)
        .group_by(
            RuleTrigger.rule_id,
            sa_func.strftime(bucket_pattern, RuleTrigger.checked_at),
        )
    ).all()
    spark_by_rule: dict[str, dict[str, int]] = defaultdict(dict)
    for r in spark_rows:
        spark_by_rule[r.rule_id][r.bucket] = int(r.fires)

    # Latest experiment_id within window (rules don't usually carry one,
    # so this is empty most of the time — still cheap).
    exp_rows = session.exec(
        sm_select(RuleTrigger.rule_id, sa_func.max(RuleTrigger.experiment_id))
        .where(RuleTrigger.checked_at >= window_start)
        .where(RuleTrigger.suppressed == 0)
        .where(RuleTrigger.experiment_id.isnot(None))
        .group_by(RuleTrigger.rule_id)
    ).all()
    exp_by_rule = {row[0]: row[1] for row in exp_rows}

    # Latest DB `source` per rule (used only when engine metadata is
    # missing — for historical rule_ids no longer in the engine).
    source_rows = session.exec(
        sm_select(RuleTrigger.rule_id, sa_func.max(RuleTrigger.source))
        .where(RuleTrigger.checked_at >= window_start)
        .where(RuleTrigger.suppressed == 0)
        .where(RuleTrigger.source.isnot(None))
        .group_by(RuleTrigger.rule_id)
    ).all()
    source_by_rule = {row[0]: row[1] for row in source_rows}

    # Top files per rule (only for rules that fired). Bounded second
    # pass — acceptable at ~tens of rules; revisit with a window
    # function if the corpus grows past hundreds.
    files_by_rule: dict[str, list[dict]] = defaultdict(list)
    for rid, row in agg.items():
        if int(row.fires) <= 0:
            continue
        file_rows = session.exec(
            sm_select(
                RuleTrigger.file_path,
                sa_func.coalesce(sa_func.sum(RuleTrigger.triggered), 0).label('n'),
            )
            .where(RuleTrigger.checked_at >= window_start)
            .where(RuleTrigger.rule_id == rid)
            .where(RuleTrigger.triggered == 1)
            .where(RuleTrigger.suppressed == 0)
            .group_by(RuleTrigger.file_path)
            .order_by(sa_func.sum(RuleTrigger.triggered).desc())
            .limit(3)
        ).all()
        files_by_rule[rid] = [
            {'name': os.path.basename(fr.file_path), 'n': int(fr.n)}
            for fr in file_rows
        ]
    return {
        'agg': agg, 'suppr_by_rule': suppr_by_rule,
        'spark_by_rule': spark_by_rule, 'exp_by_rule': exp_by_rule,
        'source_by_rule': source_by_rule, 'files_by_rule': files_by_rule,
    }


def _build_rule_row(rid: str, aggregates: dict, engine_rule, thresholds,
                     range_str: str, now) -> dict:
    """Merge per-rule DB aggregates + engine metadata into one row."""
    from lib.rules import buckets as _buckets
    from lib.rules.status import classify_status, trigger_rate_pct

    row = aggregates['agg'].get(rid)
    checks = int(row.checks) if row else 0
    fires = int(row.fires) if row else 0
    # Severity: engine metadata is authoritative.
    severity = engine_rule.severity if engine_rule else None
    # Source: engine first, DB `source` column fallback for deleted rules.
    source = engine_rule.engine if engine_rule else aggregates['source_by_rule'].get(rid)
    # Guide preview: engine metadata first (DB.guide is ~98% NULL).
    if engine_rule:
        guide_text = engine_rule.metadata.get('guide') or engine_rule.summary
    else:
        guide_text = ''
    return {
        'rule_id': rid,
        'severity': severity,
        'source': source,
        'fires': fires,
        'checks': checks,
        'trigger_rate_pct': trigger_rate_pct(fires, checks),
        'last_seen': row.last_seen if row else None,
        'status': classify_status(fires=fires, checks=checks, thresholds=thresholds),
        'spark': _buckets.zero_fill(
            aggregates['spark_by_rule'].get(rid, {}), range_str, now=now,
        ),
        'guide_preview': _truncate(guide_text, 140),
        'top_files': aggregates['files_by_rule'].get(rid, []),
        'experiment_id': aggregates['exp_by_rule'].get(rid),
        'suppressed_count': aggregates['suppr_by_rule'].get(rid, 0),
    }


def _passes_triggers_rules_filters(row: dict, params: _TriggersRulesParams) -> bool:
    if params.search and params.search not in row['rule_id'].lower():
        return False
    if params.sev_filter and (row['severity'] or '') not in params.sev_filter:
        return False
    if params.eng_filter and (row['source'] or '') not in params.eng_filter:
        return False
    if params.status_filter != 'all' and row['status'] != params.status_filter:
        return False
    if params.marks_only and not row['suppressed_count']:
        return False
    return True


_SORT_KEYS = {
    'last_seen': lambda r: (r['last_seen'] or '',),
    'rule_id':   lambda r: (r['rule_id'],),
    'fires':     lambda r: (-r['fires'], r['rule_id']),
    'rate':      lambda r: (-r['trigger_rate_pct'], -r['fires'], r['rule_id']),
}


def _sort_triggers_rules(rows: list[dict], sort_by: str) -> list[dict]:
    if sort_by == 'last_seen':
        return sorted(rows, key=_SORT_KEYS['last_seen'], reverse=True)
    key_fn = _SORT_KEYS.get(sort_by) or _SORT_KEYS['rate']
    return sorted(rows, key=key_fn)


@rules_bp.route('/api/triggers/rules')
def api_triggers_rules():
    """List per-rule health + spark + top files over the active range."""
    from datetime import datetime, timezone
    from lib.rules import buckets as _buckets
    from lib.settings import settings as _settings

    params = _parse_triggers_rules_params(
        request, _settings.rule_trigger_thresholds.default_range,
    )
    if params.range_str not in _buckets.VALID_RANGES:
        return jsonify({
            'ok': False,
            'error': f"invalid range {params.range_str!r}; expected one of "
                     f"{_buckets.VALID_RANGES}",
        }), 400

    bucket_pattern, _bucket_count, _ = _buckets.bucket_for_range(params.range_str)
    now = datetime.now(timezone.utc)
    window_start = _buckets.window_start_iso(params.range_str, now=now)
    thresholds = _settings.rule_trigger_thresholds
    engine_rules = _all_configured_rules()

    with _pkg.SessionLocal() as session:
        aggregates = _query_trigger_aggregates(session, window_start, bucket_pattern)

    # Build the per-rule output list — union of configured + observed ids.
    all_ids = set(engine_rules) | set(aggregates['agg'])
    rules_out = [
        _build_rule_row(rid, aggregates, engine_rules.get(rid),
                         thresholds, params.range_str, now)
        for rid in sorted(all_ids)
    ]

    # KPIs reflect the unfiltered population so the strip is a stable
    # orientation, not a filter echo.
    kpis = {
        'configured': len(engine_rules),
        'active': sum(1 for r in rules_out if r['status'] == 'active' and r['fires'] > 0),
        'noisy':   sum(1 for r in rules_out if r['status'] == 'noisy'),
        'dead':    sum(1 for r in rules_out if r['status'] == 'dead'),
    }

    # Apply filters after enrichment (status/severity/source need the row).
    rules_out = [r for r in rules_out if _passes_triggers_rules_filters(r, params)]
    rules_out = _sort_triggers_rules(rules_out, params.sort_by)

    return jsonify({
        'kpis': kpis,
        'rules': rules_out,
        'thresholds': thresholds.model_dump(),
        'range': params.range_str,
    })


def _detail_root_stripper(session):
    """Build the repo-prefix `_strip_root` for the drawer detail.

    Reads the registered repo roots off `session` (mirrors api_triggers)
    and returns a closure that strips the longest matching prefix off a
    file_path. Hoisted out of api_triggers_rule_detail so the loop/guard
    branches land in their own complexity block.
    """
    from sqlmodel import select as sm_select
    from lib.orm.models import Repo as _Repo

    registered = [
        r.path for r in session.exec(sm_select(_Repo)).all() if r.path
    ]
    roots = sorted(
        [p.rstrip('/') + '/' for p in registered],
        key=len, reverse=True,
    )

    def _strip_root(fp: str) -> str:
        if not fp:
            return fp
        for root in roots:
            if fp.startswith(root):
                return fp[len(root):]
        return fp

    return _strip_root


def _resolve_detail_guide(session, rule_id: str, engine_rule) -> str:
    """Resolve the drawer guide text for one rule.

    Most-recent non-null DB guide is the primary source; engine metadata
    is the fallback when no row ever carried a guide.
    """
    from sqlmodel import select as sm_select

    db_guide = session.exec(
        sm_select(RuleTrigger.guide)
        .where(RuleTrigger.rule_id == rule_id)
        .where(RuleTrigger.guide.isnot(None))
        .order_by(RuleTrigger.checked_at.desc())
        .limit(1)
    ).first()
    if db_guide is None and engine_rule is not None:
        return engine_rule.metadata.get('guide') or engine_rule.summary or ''
    return db_guide or ''


def _detail_file_rows(session, rule_id: str, window_start: str, strip_root) -> list[dict]:
    """Per-file aggregate within window. Suppressed events drop out of
    fires AND checks here too — same semantics as the list endpoint, so
    the drawer mirrors the card's headline numbers."""
    from sqlalchemy import func as sa_func
    from sqlmodel import select as sm_select

    file_rows = session.exec(
        sm_select(
            RuleTrigger.file_path,
            sa_func.count(RuleTrigger.id).label('checks'),
            sa_func.coalesce(sa_func.sum(RuleTrigger.triggered), 0).label('fires'),
            sa_func.max(RuleTrigger.checked_at).label('last_seen'),
        )
        .where(RuleTrigger.rule_id == rule_id)
        .where(RuleTrigger.checked_at >= window_start)
        .where(RuleTrigger.suppressed == 0)
        .group_by(RuleTrigger.file_path)
        .order_by(sa_func.sum(RuleTrigger.triggered).desc())
    ).all()
    return [
        {
            'file_path': strip_root(fr.file_path),
            'checks': int(fr.checks),
            'fires': int(fr.fires),
            'last_seen': fr.last_seen,
        }
        for fr in file_rows
    ]


def _detail_session_rows(session, rule_id: str, window_start: str) -> list[dict]:
    """Per-session aggregate within window. plan_filename was dropped
    from the response because Claude's auto-generated plan names carry no
    signal — the drawer surfaces last_seen instead so the user can spot
    recent vs. stale sessions at a glance."""
    from sqlalchemy import func as sa_func
    from sqlmodel import select as sm_select

    session_rows = session.exec(
        sm_select(
            sa_func.coalesce(RuleTrigger.session_id, '').label('session_id'),
            sa_func.count(RuleTrigger.id).label('checks'),
            sa_func.coalesce(sa_func.sum(RuleTrigger.triggered), 0).label('fires'),
            sa_func.max(RuleTrigger.checked_at).label('last_seen'),
        )
        .where(RuleTrigger.rule_id == rule_id)
        .where(RuleTrigger.checked_at >= window_start)
        .where(RuleTrigger.suppressed == 0)
        .group_by(sa_func.coalesce(RuleTrigger.session_id, ''))
        .order_by(sa_func.sum(RuleTrigger.triggered).desc())
    ).all()
    return [
        {
            'session_id': sr.session_id or None,
            'checks': int(sr.checks),
            'fires': int(sr.fires),
            'last_seen': sr.last_seen,
        }
        for sr in session_rows
    ]


def _detail_event_rows(session, rule_id: str, window_start: str, strip_root) -> list[dict]:
    """Recent matched events: last 20 rows where the rule actually fired.

    The /trace/triggers/raw view (with ?rule=<id>) is the escape hatch
    for the full check log including misses — this drawer focuses on the
    rows that matter, which includes suppressed ones so users can un-flag
    them here. Suppression metadata is pulled in one query.
    """
    from sqlmodel import select as sm_select
    from lib.orm.models import RuleTriggerSuppression

    event_rows = session.exec(
        sm_select(RuleTrigger)
        .where(RuleTrigger.rule_id == rule_id)
        .where(RuleTrigger.checked_at >= window_start)
        .where(RuleTrigger.triggered == 1)
        .order_by(RuleTrigger.checked_at.desc(), RuleTrigger.id.desc())
        .limit(20)
    ).all()

    event_ids = [r.id for r in event_rows]
    suppr_meta: dict[int, dict] = {}
    if event_ids:
        for s in session.exec(
            sm_select(RuleTriggerSuppression)
            .where(RuleTriggerSuppression.rule_trigger_id.in_(event_ids))
        ).all():
            suppr_meta[s.rule_trigger_id] = _suppression_to_dict(s)

    return [
        {
            'id': r.id,
            'checked_at': r.checked_at,
            'file_path': strip_root(r.file_path),
            'session_id': r.session_id,
            'span_id': r.span_id,
            'match_count': r.match_count,
            'triggered': r.triggered,
            'suppressed': bool(r.suppressed),
            'suppression': suppr_meta.get(r.id),  # null when not suppressed
        }
        for r in event_rows
    ]


@rules_bp.route('/api/triggers/rules/<rule_id>')
def api_triggers_rule_detail(rule_id: str):
    """Drawer detail for one rule: full guide + files + sessions + events."""
    from datetime import datetime, timezone
    from lib.rules import buckets as _buckets
    from lib.settings import settings as _settings

    range_str = (request.args.get('range') or _settings.rule_trigger_thresholds.default_range).lower()
    if range_str not in _buckets.VALID_RANGES:
        return jsonify({
            'ok': False,
            'error': f"invalid range {range_str!r}; expected one of "
                     f"{_buckets.VALID_RANGES}",
        }), 400
    now = datetime.now(timezone.utc)
    window_start = _buckets.window_start_iso(range_str, now=now)

    engine_rule = _all_configured_rules().get(rule_id)

    with _pkg.SessionLocal() as session:
        strip_root = _detail_root_stripper(session)
        guide = _resolve_detail_guide(session, rule_id, engine_rule)
        files = _detail_file_rows(session, rule_id, window_start, strip_root)
        sessions = _detail_session_rows(session, rule_id, window_start)
        events = _detail_event_rows(session, rule_id, window_start, strip_root)

    return jsonify({
        'rule_id': rule_id,
        'severity': engine_rule.severity if engine_rule else None,
        'source': engine_rule.engine if engine_rule else None,
        'guide': guide,
        'files': files,
        'sessions': sessions,
        'events': events,
        'range': range_str,
    })


@rules_bp.route('/api/settings/rule-triggers/thresholds')
def api_rule_trigger_thresholds():
    """Read the threshold block. Both list/drawer endpoints already echo
    these in their responses; this endpoint backs the standalone
    Settings card so it doesn't have to ping /api/triggers/rules just
    to render the form."""
    from lib.settings import settings as _settings
    return jsonify(_settings.rule_trigger_thresholds.model_dump())


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
    try:
        rate = int(body.get('noisy_min_rate_pct', 30))
        if not 0 <= rate <= 100:
            errors.append('noisy_min_rate_pct must be 0–100')
    except (TypeError, ValueError):
        errors.append('noisy_min_rate_pct must be an integer')
        rate = None
    try:
        fires = int(body.get('noisy_min_fires', 5))
        if fires < 0:
            errors.append('noisy_min_fires must be non-negative')
    except (TypeError, ValueError):
        errors.append('noisy_min_fires must be an integer')
        fires = None
    try:
        checks = int(body.get('dead_min_checks', 3))
        if checks < 1:
            errors.append('dead_min_checks must be at least 1')
    except (TypeError, ValueError):
        errors.append('dead_min_checks must be an integer')
        checks = None
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
    from sqlalchemy import update as sa_update
    from sqlmodel import select as sm_select
    from lib.orm.models import RuleTrigger, RuleTriggerSuppression
    from lib import audit
    from lib.auth import get_current_user

    user = get_current_user() or {}
    body = request.get_json(silent=True) or {}
    reason = body.get('reason')
    if reason is not None:
        reason = str(reason).strip() or None

    with _pkg.SessionLocal() as session:
        rt = session.get(RuleTrigger, trigger_id)
        if rt is None:
            return jsonify({'ok': False, 'error': 'rule_trigger not found'}), 404

        existing = session.exec(
            sm_select(RuleTriggerSuppression)
            .where(RuleTriggerSuppression.rule_trigger_id == trigger_id)
        ).first()
        if existing is not None:
            # Already suppressed. Update the reason if the caller passed
            # a new one — otherwise the second click silently drops the
            # user's text. Leaves who/when intact to preserve the
            # original authorship.
            updated = False
            if reason is not None and reason != existing.reason:
                existing.reason = reason
                session.add(existing)
                session.commit()
                session.refresh(existing)
                updated = True
            return jsonify({
                'ok': True, 'idempotent': True, 'reason_updated': updated,
                'suppression': _suppression_to_dict(existing),
            })

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


def _suppression_to_dict(row) -> dict:
    return {
        'rule_trigger_id': row.rule_trigger_id,
        'suppressed_by_id': row.suppressed_by_id,
        'suppressed_by_username': row.suppressed_by_username,
        'suppressed_at': row.suppressed_at,
        'reason': row.reason,
    }


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
