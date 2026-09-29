"""Per-rule trigger view: `/api/triggers/rules` and its per-rule drawer."""

from __future__ import annotations

import os

from dataclasses import dataclass
from flask import request, jsonify
from lib.orm.models import RuleTrigger
from web.blueprints import rules as _pkg
from web.blueprints.rules import rules_bp
from web.blueprints.rules.triggers import _suppression_to_dict


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
    args = request_obj.args
    return _TriggersRulesParams(
        range_str=_lower_arg(args, 'range', default_range),
        search=_lower_arg(args, 'search', '').strip(),
        sev_filter=_csv_set_arg(args, 'severity'),
        eng_filter=_csv_set_arg(args, 'engine'),
        status_filter=_lower_arg(args, 'status', 'all'),
        # "marks=1" → only rules with at least one suppressed event in the
        # active window. Separate from the status classification because
        # "noisy" (high trigger rate) and "user-flagged as noise" are
        # different signals — same word, different semantics.
        marks_only=args.get('marks') == '1',
        sort_by=_lower_arg(args, 'sort', 'rate'),
    )


def _lower_arg(args, name: str, default: str) -> str:
    return (args.get(name) or default).lower()


def _csv_set_arg(args, name: str) -> set:
    return {s for s in (args.get(name) or '').split(',') if s}


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


def _in_filter(value, allowed: set) -> bool:
    return not allowed or (value or '') in allowed


def _passes_triggers_rules_filters(row: dict, params: _TriggersRulesParams) -> bool:
    return (
        (not params.search or params.search in row['rule_id'].lower())
        and _in_filter(row['severity'], params.sev_filter)
        and _in_filter(row['source'], params.eng_filter)
        and params.status_filter in ('all', row['status'])
        and (not params.marks_only or bool(row['suppressed_count']))
    )


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


def _triggers_rules_kpis(engine_rules: dict, rules_out: list[dict]) -> dict:
    return {
        'configured': len(engine_rules),
        'active': sum(1 for r in rules_out if r['status'] == 'active' and r['fires'] > 0),
        'noisy':   sum(1 for r in rules_out if r['status'] == 'noisy'),
        'dead':    sum(1 for r in rules_out if r['status'] == 'dead'),
    }


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
    kpis = _triggers_rules_kpis(engine_rules, rules_out)

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
