#!/usr/bin/env python
"""Trend of time spent running tests, bucketed over calendar time.

Standalone review script — deliberately NOT wired into `regin stats` yet.
Run it, argue with the numbers, then decide whether it earns a subcommand.

    .venv/bin/python scripts/test_time_trend.py
    .venv/bin/python scripts/test_time_trend.py --bucket day --since 2026-07-01
    .venv/bin/python scripts/test_time_trend.py --format csv --out /tmp/tests.csv

Two independent measures of the same thing, reported side by side because
neither is trustworthy alone:

* **measured** — `session_spans.duration_ms`, the tool's own execution time.
  Accurate, but only ~50% of test spans carry it (older hook versions did not
  record it), so the raw sum understates. `est_h` scales it by the observed
  coverage; the extrapolation is only as good as the assumption that spans
  with and without a duration run equally long, which `--audit` checks.
* **attributed** — the inter-event gap ending at the span, the same rule
  `lib.trace.session_stats` uses everywhere. 100% coverage, but it also
  contains the model latency that preceded the call, so it overstates.

True test-execution time sits between them. If they diverge sharply in one
bucket, something other than tests changed — look before drawing a trend.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.trace.session_stats import (  # noqa: E402
    ACTIVE_GAP_MS, StatsFilter, _events_cte, _gap_expr, _query,
)

# `_events_cte` already drops the `pending-`/`promptlive-` placeholder rows
# that `session_spans` keeps for the live view. Without that, ~29% of test
# runs are counted twice — a blocking pytest call writes one row at
# PreToolUse and another at PostToolUse, both carrying the same duration_ms.

# First match wins: `npx playwright test` is E2E, not "npm".
SUITES: tuple[tuple[str, str], ...] = (
    ('playwright', r'playwright\s+test|npx\s+playwright'),
    ('pytest', r'\bpytest\b'),
    ('vitest/jest', r'\bvitest\b|\bjest\b'),
    ('npm test', r'\bnpm\s+(run\s+)?test\b'),
    ('go/cargo', r'\bgo\s+test\b|\bcargo\s+test\b'),
)
_ANY_TEST = re.compile('|'.join(p for _, p in SUITES), re.I)

_SQL = """
WITH {events},
gaps AS (
  SELECT trace_id, name, cmd, ts, dur, {gap} AS gap FROM ev WHERE 1
)
SELECT trace_id, ts, cmd, dur, gap FROM gaps
WHERE name = 'tool.Bash' AND cmd IS NOT NULL
"""

_ACTIVE_SQL = """
WITH {events},
gaps AS (SELECT ts, {gap} AS gap FROM ev WHERE prev IS NOT NULL)
SELECT ts, gap FROM gaps WHERE gap > 0 AND gap <= :active
"""


def suite_of(command: str) -> str | None:
    """Which test runner a shell command invokes, or None if it isn't one."""
    for label, pattern in SUITES:
        if re.search(pattern, command, re.I):
            return label
    return None


def bucket_of(stamp: str, size: str) -> str:
    """Calendar bucket key for an ISO timestamp."""
    if size == 'month':
        return stamp[:7]
    if size == 'day':
        return stamp[:10]
    day = date.fromisoformat(stamp[:10])
    return (day - timedelta(days=day.weekday())).isoformat()   # week starts Mon


def fetch_runs(flt: StatsFilter) -> list[dict]:
    """Every test-runner invocation in the selected sessions."""
    where, params = flt.where()
    sql = _SQL.format(
        events=_events_cte(
            where,
            "sp.name AS name, sp.duration_ms AS dur, "
            "json_extract(sp.attributes, '$.command_preview') AS cmd"),
        gap=_gap_expr())
    runs = []
    for trace_id, ts, cmd, dur, gap in _query(sql, params):
        if not cmd or not _ANY_TEST.search(cmd):
            continue
        runs.append({
            'trace_id': trace_id, 'ts': ts, 'suite': suite_of(cmd),
            'measured_ms': int(dur) if dur and dur > 0 else None,
            # A gap longer than the active threshold means the agent stalled
            # around the call, not that the suite ran that long.
            'gap_ms': int(gap) if gap and 0 < gap <= ACTIVE_GAP_MS else 0,
            'cmd': cmd.strip()[:80],
        })
    return runs


def fetch_active_ms(flt: StatsFilter) -> dict[str, int]:
    """Total agent-active ms per bucket, to express tests as a share."""
    where, params = flt.where()
    sql = _ACTIVE_SQL.format(
        events=_events_cte(where), gap=_gap_expr())
    params['active'] = ACTIVE_GAP_MS
    return {'rows': _query(sql, params)}


def _stats_for(runs: list[dict]) -> dict:
    measured = [r['measured_ms'] for r in runs if r['measured_ms'] is not None]
    total_measured = sum(measured)
    coverage = len(measured) / len(runs) if runs else 0.0
    return {
        'runs': len(runs),
        'measured_h': total_measured / 3_600_000,
        'coverage': coverage,
        'est_h': (total_measured / coverage / 3_600_000) if coverage else 0.0,
        'mean_s': (total_measured / len(measured) / 1000) if measured else 0.0,
        'p90_s': (statistics.quantiles(measured, n=10)[8] / 1000
                  if len(measured) > 9 else 0.0),
        'attributed_h': sum(r['gap_ms'] for r in runs) / 3_600_000,
        'sessions': len({r['trace_id'] for r in runs}),
    }


def build_rows(runs: list[dict], active_rows, size: str) -> list[dict]:
    """One row per calendar bucket, newest last."""
    active: dict[str, int] = {}
    for ts, gap in active_rows:
        key = bucket_of(ts, size)
        active[key] = active.get(key, 0) + int(gap or 0)
    grouped: dict[str, list[dict]] = {}
    for run in runs:
        grouped.setdefault(bucket_of(run['ts'], size), []).append(run)
    rows = []
    for key in sorted(grouped):
        row = {'bucket': key, **_stats_for(grouped[key])}
        agent_h = active.get(key, 0) / 3_600_000
        row['agent_h'] = agent_h
        row['share'] = row['est_h'] / agent_h if agent_h else 0.0
        row['runs_per_session'] = row['runs'] / max(1, row['sessions'])
        rows.append(row)
    return rows


def suite_rows(runs: list[dict]) -> list[dict]:
    by_suite: dict[str, list[dict]] = {}
    for run in runs:
        by_suite.setdefault(run['suite'] or 'other', []).append(run)
    rows = [{'suite': k, **_stats_for(v)} for k, v in by_suite.items()]
    return sorted(rows, key=lambda r: -r['est_h'])


def slowest_rows(runs: list[dict], limit: int = 12) -> list[dict]:
    timed = [r for r in runs if r['measured_ms']]
    timed.sort(key=lambda r: -r['measured_ms'])
    return [{'seconds': r['measured_ms'] / 1000, 'suite': r['suite'] or 'other',
             'command': r['cmd']} for r in timed[:limit]]


def _median_gap(runs: list[dict]) -> float:
    gaps = [r['gap_ms'] for r in runs if r['gap_ms']]
    return statistics.median(gaps) / 1000 if gaps else 0.0


def _audit_suite_row(suite: str, timed: list[dict], untimed: list[dict]) -> str:
    t = [r['measured_ms'] for r in timed if (r['suite'] or 'other') == suite]
    u = [r for r in untimed if (r['suite'] or 'other') == suite]
    mean = statistics.mean(t) / 1000 if t else 0.0
    return f"{suite:14} {len(t):7} {len(u):8} {mean:13.1f}"


def audit(runs: list[dict]) -> None:
    """Sanity-check the extrapolation the trend table leans on.

    `est_h` assumes runs missing `duration_ms` are like the ones that have it.
    That holds only if both populations share a suite mix and a similar
    attributed gap; this prints both so the assumption can be rejected.
    """
    timed = [r for r in runs if r['measured_ms']]
    untimed = [r for r in runs if not r['measured_ms']]
    print(f"\nAudit: {len(timed)} runs carry duration_ms, {len(untimed)} do not.")
    if not timed or not untimed:
        return
    print("If the two populations differ in composition, est_h is biased.")
    print(f"{'suite':14} {'timed':>7} {'untimed':>8} {'timed mean s':>13}")
    for suite in sorted({r['suite'] or 'other' for r in runs}):
        print(_audit_suite_row(suite, timed, untimed))
    print(f"\nMedian attributed gap: timed {_median_gap(timed):.1f}s vs "
          f"untimed {_median_gap(untimed):.1f}s — if these differ a lot, the "
          f"untimed runs are not comparable and est_h should not be trusted.")


def print_table(rows: list[dict], size: str) -> None:
    print(f"Test-run time by {size}\n")
    print(f"{'bucket':11} {'runs':>5} {'meas_h':>7} {'cov':>5} {'est_h':>6} "
          f"{'agent_h':>8} {'share':>6} {'mean_s':>7} {'p90_s':>7} "
          f"{'attr_h':>7} {'runs/sess':>10}")
    print('-' * 92)
    blind = 0
    for r in rows:
        # Coverage 0 means the hook never recorded duration_ms that week, not
        # that tests were free — printing 0.0 h would read as a real low.
        cells = (f"{r['est_h']:6.1f} {r['agent_h']:8.1f} {r['share'] * 100:5.0f}% "
                 f"{r['mean_s']:7.1f} {r['p90_s']:7.1f}")
        if not r['coverage']:
            blind += 1
            cells = f"{'n/a':>6} {r['agent_h']:8.1f} {'n/a':>6} {'n/a':>7} {'n/a':>7}"
        print(f"{r['bucket']:11} {r['runs']:5} {r['measured_h']:7.1f} "
              f"{r['coverage'] * 100:4.0f}% {cells} {r['attributed_h']:7.1f} "
              f"{r['runs_per_session']:10.1f}")
    if blind:
        print(f"\n{blind} bucket(s) predate duration_ms capture — 'n/a', not zero. "
              f"Use attr_h (gap-attributed) to compare those weeks.")


def print_suites(rows: list[dict]) -> None:
    print(f"\nBy suite\n\n{'suite':14} {'runs':>6} {'est_h':>7} {'mean_s':>8} "
          f"{'p90_s':>8} {'cov':>5}")
    print('-' * 52)
    for r in rows:
        print(f"{r['suite']:14} {r['runs']:6} {r['est_h']:7.1f} "
              f"{r['mean_s']:8.1f} {r['p90_s']:8.1f} {r['coverage'] * 100:4.0f}%")


def print_slowest(rows: list[dict]) -> None:
    print(f"\nSlowest individual runs\n\n{'sec':>7}  {'suite':12} command")
    print('-' * 92)
    for r in rows:
        print(f"{r['seconds']:7.0f}  {r['suite']:12} {r['command']}")


def emit(payload, rows, fmt: str, out: str | None) -> None:
    if fmt == 'csv':
        buf = sys.stdout if not out else open(out, 'w', newline='', encoding='utf-8')
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        if out:
            buf.close()
            print(f'wrote {out}')
        return
    body = json.dumps(payload, indent=2, default=str)
    if out:
        Path(out).write_text(body + '\n', encoding='utf-8')
        print(f'wrote {out}')
    else:
        print(body)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--since', help='sessions started on/after YYYY-MM-DD')
    p.add_argument('--until', help='sessions started before YYYY-MM-DD')
    p.add_argument('--project', help='substring match on the session cwd')
    p.add_argument('--bucket', default='week', choices=('day', 'week', 'month'))
    p.add_argument('--format', default='table', choices=('table', 'csv', 'json'))
    p.add_argument('--out', help='write to this file instead of stdout')
    p.add_argument('--audit', action='store_true',
                   help='check whether the duration_ms extrapolation is sound')
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    flt = StatsFilter(since=args.since, until=args.until, project=args.project)
    runs = fetch_runs(flt)
    if not runs:
        print('No test runs matched the filter.', file=sys.stderr)
        return 1
    rows = build_rows(runs, fetch_active_ms(flt)['rows'], args.bucket)
    suites = suite_rows(runs)
    slowest = slowest_rows(runs)
    if args.format == 'table':
        print_table(rows, args.bucket)
        print_suites(suites)
        print_slowest(slowest)
        if args.audit:
            audit(runs)
        return 0
    emit({'trend': rows, 'suites': suites, 'slowest': slowest},
         rows, args.format, args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
