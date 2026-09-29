"""Characterization of `lib.features.ledger.render` — the exact text the
`feature ledger` command prints for a fully-populated and a sparse report."""

from __future__ import annotations

from types import SimpleNamespace as NS

from lib.features.ledger import SessionRow, render


def _report(**over):
    base = dict(
        id=7, title="a feature", external_ref="ENG-1", stage="building",
        rounds=2, open_defects=1, total_defects=3, escapes=NS(escapes=1),
        sync_error="tracker down",
        cost=NS(recalled=1, usd=12.5, sessions=3, shared=2,
                gaps={"unpriced": 1, "zero": 0}),
        work_time=NS(active_work_ms=3_723_000, gaps={"no_trace": 2}),
        human_prompts=NS(prompts=9, sessions=2, run_sessions=1,
                         gaps={"unknown_origin": 1}),
        tokens={"input": 1200, "cache_read": 34000},
        rows=[SessionRow(trace_id="abcdef1234567890", date="2026-09-01",
                         title="first session", cost_usd=4.25,
                         provenance="declared", round=1, is_primary=1),
              SessionRow(trace_id="0123456789abcdef", date="2026-09-02",
                         title="recalled", cost_usd=None,
                         provenance="recalled", round=None, is_primary=0)],
        traceless_runs=1,
        coverage=NS(declared_sessions=3, all_sessions=4,
                    declared_usd=12.5, all_usd=20.0),
    )
    base.update(over)
    return NS(**base)


def test_render_full_report():
    assert render(_report()) == FULL


def test_render_sparse_report():
    sparse = _report(
        external_ref=None, sync_error=None,
        cost=NS(recalled=0, usd=None, sessions=0, shared=0, gaps={}),
        work_time=NS(active_work_ms=0, gaps={}),
        human_prompts=NS(prompts=0, sessions=0, run_sessions=0, gaps={}),
        tokens={}, rows=[], traceless_runs=0,
        coverage=NS(declared_sessions=0, all_sessions=0,
                    declared_usd=0.0, all_usd=0.0),
    )
    assert render(sparse) == SPARSE


FULL = [
    '#7  a feature',
    'building  rounds=2  open_defects=1  escapes=1  ENG-1',
    '  being built',
    '  sync: tracker down',
    '',
    'cost (upper bound)  $12.50 across 3 sessions (1 recalled)',
    '    2 shared sessions, cost charged elsewhere',
    '    gaps: unpriced 1',
    'work time  1h 02m',
    '    gaps: no-trace 2',
    'human prompts  9 across 2 human sessions',
    '    1 session excluded, produced by a task run',
    '    gaps: unknown-origin 1',
    'tokens  input 1,200 · cache-read 34,000',
    '',
    'sessions',
    '  2026-09-01  abcdef12  first session                                      '
    '$4.25  declared   round 1',
    '  2026-09-02  01234567  recalled                                      '
    'none declared  recalled   round —',
    '    gaps: traceless-runs 1',
    '',
    'defects  1 open / 3 total      escapes 1',
    '',
    'coverage  3 of 4 sessions (75.0%) · $12.50 of $20.00 (62.5%)',
]

SPARSE = [
    '#7  a feature',
    'building  rounds=2  open_defects=1  escapes=1',
    '  being built',
    '',
    'cost (upper bound)  none declared',
    'work time  —',
    'human prompts  0 across 0 human sessions',
    'tokens  ',
    '',
    'sessions',
    '  none declared',
    '',
    'defects  1 open / 3 total      escapes 1',
    '',
    'coverage  0 of 0 sessions (—) · $0.00 of $0.00 (—)',
]
