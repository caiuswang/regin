"""Cross-session time accounting: session rows, activity / command breakdowns,
engaged windows and their aggregation. See `lib.trace.session_stats`."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional, Sequence
from sqlalchemy import bindparam, text

from lib.orm import SessionLocal
from lib.trace.pending_spans import parse_naive_ts


_ONE_DAY = timedelta(days=1)

ACTIVE_GAP_MS = 60_000


ENGAGED_GAP_MS = 300_000

# `tool.apply_patch` is Codex's edit span. It carries no `file_path` (460 rows,
# none with a path), so it counts toward edit totals but cannot feed the
# per-file rework lens — `_blind_spots` says so rather than reporting zero.
_EDIT_NAMES = ('tool.Edit', 'tool.Write', 'tool.NotebookEdit',
               'tool.MultiEdit', 'tool.apply_patch')

# Span names that wrap other spans rather than mark work of their own. Gaps
# ending at one of these describe the wrapped work, not the envelope, so they
# are dropped from activity attribution (but still counted as engaged time).
CONTAINER_SPANS = (
    'prompt', 'conversation', 'session.start', 'session.end',
    'compact.pre', 'compact.post', 'rewind', 'subagent.start', 'subagent.stop',
    'task.notification',
)

_ACTIVITY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('thinking', ('assistant.thinking',)),
    ('responding', ('assistant_response', 'turn')),
    ('editing', ('tool.Edit', 'tool.Write', 'tool.NotebookEdit',
                 'tool.MultiEdit')),
    ('reading', ('tool.Read', 'tool.Glob', 'tool.Grep', 'tool.ToolSearch',
                 'tool.NotebookRead')),
    ('web', ('tool.WebFetch', 'tool.WebSearch')),
    ('shell', ('tool.Bash', 'tool.BashOutput', 'tool.KillShell')),
    ('delegating', ('tool.Agent', 'tool.Task', 'tool.Workflow', 'tool.Skill')),
    ('planning', ('tool.TaskCreate', 'tool.TaskUpdate', 'tool.ExitPlanMode',
                  'tool.EnterPlanMode', 'plan.enter')),
    ('failure-recovery', ('tool.failure',)),
    ('waiting-on-human', ('permission.request', 'tool.AskUserQuestion')),
)

_ACTIVITY_BY_NAME = {
    name: label for label, names in _ACTIVITY_RULES for name in names
}

# Ordered longest-intent-first: the first pattern that matches a command
# preview wins, so `npx vite build` classifies as a build, not as `npx`.
_COMMAND_RULES: tuple[tuple[str, str], ...] = (
    ('tests', r'\b(pytest|playwright test|vitest|jest|npm test|go test|cargo test)\b'),
    ('build', r'\b(vite build|npm run build|tsc|webpack|make\b|cargo build)\b'),
    ('git', r'\bgit\b'),
    ('lint/format', r'\b(ruff|radon|eslint|prettier|mypy|black|grit)\b'),
    ('db/sqlite', r'\b(sqlite3|alembic)\b'),
    # Anchored to an invocation position: a bare \bregin\b would match the
    # repo path, which appears in nearly every command run from this tree.
    ('regin cli', r'cli/regin\.py|(?:^|[;&|]\s*)regin\s'),
    ('browser/e2e', r'\b(playwright|dom-measure|chromium)\b'),
    ('search/inspect', r'\b(grep|rg|find|ls|cat|head|tail|wc|sed|awk)\b'),
    ('python', r'\bpython\b'),
    ('node/npm', r'\b(node|npx|npm|pnpm|yarn)\b'),
)


@dataclass
class SessionStat:
    """One real session, with the time decomposition and its cost drivers."""

    trace_id: str
    title: Optional[str]
    started_at: str
    last_seen: str
    day: str
    cwd: Optional[str]
    project: str
    model: Optional[str]
    agent_type: Optional[str]
    origin: Optional[str]
    wall_ms: int
    engaged_ms: int
    active_ms: int
    human_ms: int
    agent_ms: int
    prompts: int
    tool_calls: int
    edits: int
    files_touched: int
    rework_edits: int
    failures: int
    cost_usd: float
    input_tokens: int
    output_tokens: int

    @property
    def engaged_hours(self) -> float:
        return self.engaged_ms / 3_600_000

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StatsFilter:
    """Which sessions count as *real* work.

    Defaults exclude harness-generated traces: `is_test` markers, LLM staging
    runs, topic-proposal agents, and workflow fan-out (whose time is already
    counted inside the parent session that launched it).
    """

    since: Optional[str] = None
    until: Optional[str] = None
    origins: Sequence[str] = ('session',)
    include_test: bool = False
    project: Optional[str] = None
    min_engaged_ms: int = 60_000
    trace_id: Optional[str] = None

    def where(self) -> tuple[str, dict[str, Any]]:
        clauses = ["COALESCE(s.origin, 'session') IN :origins"]
        params: dict[str, Any] = {'origins': tuple(self.origins)}
        if self.trace_id:
            # Narrows the scan to one session; without it `diagnose_session`
            # would re-aggregate every trace in the DB per call.
            clauses.append('s.trace_id = :trace_id')
            params['trace_id'] = self.trace_id
        if not self.include_test:
            clauses.append('s.is_test = 0')
        if self.since:
            clauses.append('s.started_at >= :since')
            params['since'] = self.since
        if self.until:
            clauses.append('s.started_at < :until')
            params['until'] = self.until
        if self.project:
            clauses.append('s.cwd LIKE :project')
            params['project'] = f'%{self.project}%'
        return ' AND '.join(clauses), params


def _live_filter(alias: str = 'sp') -> str:
    """Exclude the live placeholder rows the merge layer retires at read time.

    `session_spans` is append-only: a blocking tool writes a `pending-<tu>`
    row at PreToolUse and a resolved row at PostToolUse, and a live prompt
    writes `promptlive-<hash>` before its real anchor lands.
    `lib/trace/merge.py` drops the superseded rows when serving the UI, so any
    raw aggregate reader must do the same or it double-counts — 12% of all
    spans are placeholders, and counting both halves of a pair inflates tool
    counts and splits one gap into two.
    """
    return (
        f"COALESCE({alias}.status_code, '') <> 'PENDING' "
        f"AND {alias}.span_id NOT LIKE 'pending-%' "
        f"AND {alias}.span_id NOT LIKE 'permreq-%' "
        f"AND {alias}.span_id NOT LIKE 'promptlive-%'"
    )


def _gap_expr(prev: str = 'prev', cur: str = 'ts') -> str:
    """Milliseconds between two ISO timestamps.

    `unixepoch(…, 'subsec')` rather than differencing `julianday()`: julian
    day numbers are ~2.46e6, so a float subtraction scaled back to
    milliseconds drifts by a millisecond or two per gap, which compounds
    across hundreds of thousands of them. Both forms honour a tz offset.
    Requires SQLite ≥ 3.42 for the 'subsec' modifier.
    """
    return (f"CAST(ROUND((unixepoch({cur}, 'subsec') - "
            f"unixepoch({prev}, 'subsec')) * 1000) AS INTEGER)")


# A gap belongs to the human only if the event that ENDS it is the user
# acting: they typed a prompt, or they answered a question the agent was
# blocked on. Subagent launch prompts (`prompt-sa-`, or attributes.agent_id)
# are the agent prompting itself and must not count.
_USER_PROMPT = (
    "sp.name = 'prompt' AND sp.span_id NOT LIKE 'prompt-sa-%' "
    "AND COALESCE(json_extract(sp.attributes, '$.agent_id'), '') = ''"
)


_BLOCKING_ASK = ('permission.request', 'tool.AskUserQuestion')


def _events_cte(where: str, extra_cols: str = '') -> str:
    """Timeline of every span in the selected sessions, with the prior stamp.

    `is_human` marks gaps the *user* owns; `prev_ask` marks a gap that follows
    a question or approval request, which the user also owns. Everything else
    is the agent or the harness working, however long it took.
    """
    cols = f', {extra_cols}' if extra_cols else ''
    asks = ', '.join(f"'{n}'" for n in _BLOCKING_ASK)
    return f"""
    sel AS (SELECT s.trace_id FROM sessions s WHERE {where}),
    ev AS (
      SELECT sp.trace_id, sp.start_time AS ts, sp.id AS row_id{cols},
             CASE WHEN {_USER_PROMPT} THEN 1 ELSE 0 END AS is_human,
             LAG(CASE WHEN sp.name IN ({asks}) THEN 1 ELSE 0 END) OVER (
               PARTITION BY sp.trace_id ORDER BY sp.start_time, sp.id
             ) AS prev_ask,
             LAG(sp.start_time) OVER (
               PARTITION BY sp.trace_id ORDER BY sp.start_time, sp.id
             ) AS prev
      FROM session_spans sp
      JOIN sel ON sel.trace_id = sp.trace_id
      WHERE {_live_filter()}
    )
    """


_SESSION_SQL = """
WITH {events},
gaps AS (
  SELECT trace_id, {gap} AS gap,
         (is_human = 1 OR prev_ask = 1) AS human
  FROM ev WHERE prev IS NOT NULL
),
t AS (
  SELECT trace_id,
         SUM(CASE WHEN gap > 0 THEN gap ELSE 0 END) AS wall_ms,
         SUM(CASE WHEN gap > 0 AND gap <= :engaged THEN gap ELSE 0 END) AS engaged_ms,
         SUM(CASE WHEN gap > 0 AND gap <= :active THEN gap ELSE 0 END) AS active_ms,
         SUM(CASE WHEN gap > 0 AND gap <= :engaged AND human
                  THEN gap ELSE 0 END) AS human_ms
  FROM gaps GROUP BY trace_id
),
all_edits AS (
  SELECT sp.trace_id, COUNT(*) AS edits
  FROM session_spans sp JOIN sel ON sel.trace_id = sp.trace_id
  WHERE sp.name IN {edit_names} AND {live}
  GROUP BY 1
),
edits AS (
  SELECT sp.trace_id,
         json_extract(sp.attributes, '$.file_path') AS fp,
         COUNT(*) AS n
  FROM session_spans sp JOIN sel ON sel.trace_id = sp.trace_id
  WHERE sp.name IN {edit_names}
    AND json_extract(sp.attributes, '$.file_path') IS NOT NULL
    AND {live}
  GROUP BY 1, 2
),
churn AS (
  SELECT trace_id, COUNT(*) AS files_touched,
         SUM(CASE WHEN n > 1 THEN n - 1 ELSE 0 END) AS rework_edits
  FROM edits GROUP BY trace_id
),
fails AS (
  SELECT sp.trace_id, COUNT(*) AS failures
  FROM session_spans sp JOIN sel ON sel.trace_id = sp.trace_id
  WHERE sp.name = 'tool.failure' AND {live} GROUP BY 1
)
SELECT s.trace_id, s.title, s.started_at, s.last_seen, s.cwd, s.model,
       s.agent_type, s.origin, s.prompts, s.tool_calls,
       COALESCE(t.wall_ms, 0), COALESCE(t.engaged_ms, 0), COALESCE(t.active_ms, 0),
       COALESCE(t.human_ms, 0),
       COALESCE(all_edits.edits, 0), COALESCE(churn.files_touched, 0),
       COALESCE(churn.rework_edits, 0), COALESCE(fails.failures, 0),
       COALESCE(s.cost_usd, 0.0), COALESCE(s.input_tokens, 0),
       COALESCE(s.output_tokens, 0)
FROM sessions s
JOIN t ON t.trace_id = s.trace_id
LEFT JOIN all_edits ON all_edits.trace_id = s.trace_id
LEFT JOIN churn ON churn.trace_id = s.trace_id
LEFT JOIN fails ON fails.trace_id = s.trace_id
WHERE t.engaged_ms >= :min_engaged
ORDER BY s.started_at
"""


def _project_of(cwd: Optional[str]) -> str:
    """Collapse a working directory to a project label.

    Scratchpad and worktree paths carry a session UUID, so grouping on raw
    `cwd` would scatter one project across hundreds of singleton rows.
    """
    if not cwd:
        return '(unknown)'
    parts = [p for p in cwd.split('/') if p]
    for part in parts:
        # Harness scratchpads live under a slugified copy of the originating
        # repo path (`-Users-taowang-regin/<uuid>/scratchpad/…`); without this
        # every session would bucket under its own UUID directory.
        if part.startswith('-Users-'):
            slug = part.split('-', 3)
            return slug[3] if len(slug) > 3 else part
    if parts[0] == 'Users' and len(parts) > 2:
        return parts[2]
    return parts[-1]


def _to_session_stat(row: Sequence[Any]) -> SessionStat:
    engaged, human = int(row[11]), int(row[13])
    return SessionStat(
        trace_id=row[0], title=row[1], started_at=row[2], last_seen=row[3],
        day=(row[2] or '')[:10], cwd=row[4], project=_project_of(row[4]),
        model=row[5], agent_type=row[6], origin=row[7],
        prompts=int(row[8] or 0), tool_calls=int(row[9] or 0),
        wall_ms=int(row[10]), engaged_ms=engaged, active_ms=int(row[12]),
        human_ms=human, agent_ms=max(0, engaged - human),
        edits=int(row[14]), files_touched=int(row[15]),
        rework_edits=int(row[16]), failures=int(row[17]),
        cost_usd=float(row[18]), input_tokens=int(row[19]),
        output_tokens=int(row[20]),
    )


def _query(sql: str, params: dict[str, Any]) -> list[Sequence[Any]]:
    stmt = text(sql)
    if 'origins' in params:
        stmt = stmt.bindparams(bindparam('origins', expanding=True))
    with SessionLocal() as db:
        return list(db.execute(stmt, params).all())


def load_sessions(flt: Optional[StatsFilter] = None) -> list[SessionStat]:
    """Per-session time decomposition for every session matching `flt`."""
    flt = flt or StatsFilter()
    where, params = flt.where()
    sql = _SESSION_SQL.format(events=_events_cte(where), gap=_gap_expr(),
                              live=_live_filter(), edit_names=_EDIT_NAMES)
    params.update(engaged=ENGAGED_GAP_MS, active=ACTIVE_GAP_MS,
                  min_engaged=flt.min_engaged_ms)
    return [_to_session_stat(r) for r in _query(sql, params)]


_ACTIVITY_SQL = """
WITH {events},
gaps AS (
  SELECT name, {gap} AS gap FROM ev WHERE prev IS NOT NULL
)
SELECT name, COUNT(*), SUM(gap) FROM gaps
WHERE gap > 0 AND gap <= :active
GROUP BY name
"""


def _activity_of(span_name: str) -> str:
    if span_name in _ACTIVITY_BY_NAME:
        return _ACTIVITY_BY_NAME[span_name]
    if span_name.startswith('tool.mcp__'):
        return 'mcp tools'
    if span_name.startswith('tool.'):
        return 'other tools'
    if span_name.startswith(('hook.', 'rule.', 'harness.', 'instructions.',
                             'config.', 'environment.', 'cwd.', 'permission.')):
        return 'harness overhead'
    return 'other'


def load_activity(flt: Optional[StatsFilter] = None) -> list[dict[str, Any]]:
    """Active time grouped by what the agent was doing, descending by time."""
    flt = flt or StatsFilter()
    where, params = flt.where()
    sql = _ACTIVITY_SQL.format(
        events=_events_cte(where, 'sp.name AS name'), gap=_gap_expr())
    params['active'] = ACTIVE_GAP_MS
    buckets: dict[str, list[int]] = {}
    for name, count, total in _query(sql, params):
        if name in CONTAINER_SPANS:
            continue
        slot = buckets.setdefault(_activity_of(name), [0, 0])
        slot[0] += int(count or 0)
        slot[1] += int(total or 0)
    rows = [{'activity': k, 'events': v[0], 'ms': v[1]}
            for k, v in buckets.items()]
    return sorted(rows, key=lambda r: -r['ms'])


_COMMAND_SQL = """
WITH {events},
gaps AS (
  SELECT name, cmd, {gap} AS gap FROM ev WHERE prev IS NOT NULL
)
SELECT cmd, gap FROM gaps
WHERE name = 'tool.Bash' AND gap > 0 AND gap <= :active AND cmd IS NOT NULL
"""


def classify_command(preview: str) -> str:
    """Bucket a shell command by intent (tests, git, build, …)."""
    text_ = (preview or '').strip().lower()
    for label, pattern in _COMMAND_RULES:
        if re.search(pattern, text_):
            return label
    return 'other shell'


def load_commands(flt: Optional[StatsFilter] = None) -> list[dict[str, Any]]:
    """Shell time split by command intent — the biggest single time sink."""
    flt = flt or StatsFilter()
    where, params = flt.where()
    sql = _COMMAND_SQL.format(
        events=_events_cte(
            where,
            "sp.name AS name, "
            "json_extract(sp.attributes, '$.command_preview') AS cmd"),
        gap=_gap_expr())
    params['active'] = ACTIVE_GAP_MS
    buckets: dict[str, list[int]] = {}
    for cmd, gap in _query(sql, params):
        slot = buckets.setdefault(classify_command(cmd), [0, 0])
        slot[0] += 1
        slot[1] += int(gap or 0)
    rows = [{'command': k, 'calls': v[0], 'ms': v[1]}
            for k, v in buckets.items()]
    return sorted(rows, key=lambda r: -r['ms'])


_WINDOW_SQL = """
WITH {events},
marked AS (
  SELECT trace_id, ts, prev,
         CASE WHEN prev IS NULL OR {gap} > :engaged THEN 1 ELSE 0 END AS is_new
  FROM ev
),
grouped AS (
  SELECT trace_id, ts,
         SUM(is_new) OVER (PARTITION BY trace_id ORDER BY ts
                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS win
  FROM marked
)
SELECT MIN(ts), MAX(ts) FROM grouped
GROUP BY trace_id, win
HAVING MIN(ts) < MAX(ts)
"""


def engaged_windows(flt: Optional[StatsFilter] = None
                    ) -> list[tuple[datetime, datetime]]:
    """Contiguous attended stretches — events separated by ≤ `ENGAGED_GAP_MS`."""
    flt = flt or StatsFilter()
    where, params = flt.where()
    sql = _WINDOW_SQL.format(events=_events_cte(where), gap=_gap_expr())
    params['engaged'] = ENGAGED_GAP_MS
    out = []
    for start, end in _query(sql, params):
        # Some traces store tz-aware stamps and others naive server-local;
        # merging the two timelines requires one awareness. parse_naive_ts
        # converts aware stamps to local naive.
        first, last = parse_naive_ts(start), parse_naive_ts(end)
        if first and last and first < last:
            out.append((first, last))
    return sorted(out)


def merge_intervals(windows: Iterable[tuple[datetime, datetime]]
                    ) -> list[tuple[datetime, datetime]]:
    """Union of possibly-overlapping intervals, so parallel sessions count once."""
    merged: list[list[datetime]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def split_by_day(windows: Iterable[tuple[datetime, datetime]]
                 ) -> dict[str, int]:
    """Milliseconds of wall time per calendar day, splitting midnight-crossers."""
    per_day: dict[str, int] = {}
    for start, end in windows:
        cursor = start
        while cursor < end:
            next_day = datetime.combine(cursor.date(), datetime.min.time()) + _ONE_DAY
            stop = min(end, next_day)
            key = cursor.date().isoformat()
            per_day[key] = per_day.get(key, 0) + int(
                (stop - cursor).total_seconds() * 1000)
            cursor = stop
    return dict(sorted(per_day.items()))


@dataclass
class Totals:
    """Headline numbers for a filtered slice of sessions."""

    sessions: int = 0
    wall_ms: int = 0
    engaged_ms: int = 0
    active_ms: int = 0
    human_ms: int = 0
    agent_ms: int = 0
    calendar_ms: int = 0
    prompts: int = 0
    edits: int = 0
    rework_edits: int = 0
    failures: int = 0
    cost_usd: float = 0.0
    days: int = 0
    per_day_ms: dict[str, int] = field(default_factory=dict)

    @property
    def concurrency(self) -> float:
        """Mean parallel sessions: engaged time divided by calendar time."""
        return self.engaged_ms / self.calendar_ms if self.calendar_ms else 0.0

    @property
    def agent_share(self) -> float:
        return self.agent_ms / self.engaged_ms if self.engaged_ms else 0.0


_TOTALLED = ('wall_ms', 'engaged_ms', 'active_ms', 'human_ms', 'agent_ms',
             'prompts', 'edits', 'rework_edits', 'failures', 'cost_usd')


def summarize(sessions: Sequence[SessionStat],
              windows: Sequence[tuple[datetime, datetime]]) -> Totals:
    """Roll per-session rows plus the merged timeline into headline numbers."""
    per_day = split_by_day(merge_intervals(windows))
    summed = {name: 0 for name in _TOTALLED}
    for s in sessions:
        for name in _TOTALLED:
            summed[name] += getattr(s, name)
    return Totals(sessions=len(sessions), calendar_ms=sum(per_day.values()),
                  days=len(per_day), per_day_ms=per_day, **summed)


def group_by(sessions: Sequence[SessionStat], key: str) -> list[dict[str, Any]]:
    """Aggregate session rows on `day`, `project`, `model`, or `agent_type`."""
    buckets: dict[str, dict[str, Any]] = {}
    for s in sessions:
        label = str(getattr(s, key, None) or '(none)')
        slot = buckets.setdefault(label, {
            key: label, 'sessions': 0, 'engaged_ms': 0, 'active_ms': 0,
            'human_ms': 0, 'agent_ms': 0, 'prompts': 0, 'edits': 0,
            'rework_edits': 0,
            'failures': 0, 'cost_usd': 0.0})
        slot['sessions'] += 1
        for f in ('engaged_ms', 'active_ms', 'human_ms', 'agent_ms', 'prompts', 'edits',
                  'rework_edits', 'failures', 'cost_usd'):
            slot[f] += getattr(s, f)
    return sorted(buckets.values(), key=lambda r: -r['engaged_ms'])
