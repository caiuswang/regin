"""Engineering-time analytics over recorded sessions.

Answers "where did the time actually go?" from the span timeline, at three
nested scopes:

* **wall** — first to last event of a session; includes overnight gaps, so it
  is only useful as a denominator.
* **engaged** — sum of inter-event gaps ≤ `ENGAGED_GAP_MS`. A human watching
  an agent, reading a diff, or typing the next prompt still produces events
  within a few minutes of each other, so this is the closest proxy for
  attended time at the keyboard.
* **active** — sum of gaps ≤ `ACTIVE_GAP_MS`, the same rule
  `lib.trace.projection._compute_active_work_ms` persists on
  `sessions.active_work_ms`. This is the agent operating.

Gaps are attributed to the span that *ends* them, because tool and turn spans
are emitted after the work completes (PostToolUse fires on return), so the
interval preceding an event is the work that produced it. Summing span widths
instead would read zero for most tool spans.

Sessions run concurrently, so per-session engaged time double-counts. Use
`merge_intervals` over `engaged_windows` for real calendar time; the ratio of
the two is how many agents were running at once.

Two known divergences from the number the trace UI shows for a session:
`_compute_active_work_ms` also drops spans that have children, and it treats a
narrower set of names as containers. Counting container spans as gap endpoints
splits some >60s gaps into countable ≤60s pieces, so `active_ms` here reads
~0.6% high at the median and ~8% at p90 against the stored
`sessions.active_work_ms`. Deliberate: a gap should be charged to whatever
event ended it, including a prompt.

`human_ms` is NOT `engaged - active`. A duration cutoff cannot tell a human
apart from a slow agent: 43 h of >60 s gaps across the corpus end at
`assistant.thinking` and 17 h end at a long `tool.Bash`, none of which is a
person. Human time is therefore event-derived — the gap ending at a *user*
prompt (excluding subagent launch prompts), plus the gap after a question or
approval request the agent was blocked on. `agent_ms` is the remainder of
attended time.
"""

from __future__ import annotations

import json
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


# --- single-session postmortem ---------------------------------------------

_TIMELINE_SQL = """
SELECT sp.name, sp.start_time, sp.attributes, sp.status_code, sp.span_id,
       COALESCE(sp.agent_id, '')
FROM session_spans sp WHERE sp.trace_id = :tid AND {live}
ORDER BY sp.start_time, sp.id
"""

# How many spans of context to show around an unexplained stall.
SLOWEST_OPS = 12


@dataclass
class Segment:
    """One user request and everything the agent did before the next one."""

    index: int
    prompt: str
    started_at: str
    engaged_ms: int = 0
    active_ms: int = 0
    edits: int = 0
    failures: int = 0
    tools: int = 0
    top_activity: str = ''


@dataclass
class SessionDetail:
    """Why one session took as long as it did."""

    stat: SessionStat
    segments: list[Segment]
    activity: list[dict[str, Any]]
    files: list[dict[str, Any]]
    slowest: list[dict[str, Any]]
    failures: list[dict[str, Any]]


def _single_session_filter(trace_id: str) -> StatsFilter:
    """Match exactly one session, whatever its origin.

    A postmortem is always requested by id, so the "real session" defaults
    must not hide a workflow or test trace the caller explicitly named.
    """
    return StatsFilter(
        trace_id=trace_id, min_engaged_ms=0, include_test=True,
        origins=('session', 'workflow', 'llm-stage', 'topic-proposal'))


def _fetch_timeline(trace_id: str) -> list[dict[str, Any]]:
    rows = _query(_TIMELINE_SQL.format(live=_live_filter()), {'tid': trace_id})
    out = []
    for name, start, attrs, status, span_id, agent_id in rows:
        ts = parse_naive_ts(start)
        if ts is None:
            continue
        try:
            parsed = json.loads(attrs) if attrs else {}
        except (TypeError, ValueError):
            parsed = {}
        out.append({'name': name, 'ts': ts, 'attrs': parsed, 'status': status,
                    'span_id': span_id or '', 'agent_id': agent_id or ''})
    return out


def is_user_prompt(event: dict) -> bool:
    """True for a prompt the human typed, not a subagent launch prompt."""
    return (event['name'] == 'prompt'
            and not str(event.get('span_id', '')).startswith('prompt-sa-')
            and not event['attrs'].get('agent_id'))


def _label_for(event: dict[str, Any]) -> str:
    """Human-readable label for one timeline event."""
    attrs = event['attrs']
    preview = attrs.get('command_preview') or attrs.get('file_path')
    if preview:
        return f"{event['name']}  {str(preview).splitlines()[0][:70]}"
    return event['name']


def _new_segment(event: dict[str, Any], index: int) -> Segment:
    text_ = (event['attrs'].get('text') or '').strip().replace('\n', ' ')
    return Segment(index=index, prompt=text_ or '(no text)',
                   started_at=event['ts'].isoformat(timespec='seconds'))


def _tally(event: dict[str, Any], gap: int, seg: Optional[Segment],
           acts: dict[str, int], seg_acts: dict[int, dict[str, int]]) -> None:
    """Charge one gap to its activity bucket and to the open segment.

    Segments accrue the full attended gap (a request's cost includes the time
    the human spent reading its output), but activity attribution stays on the
    ≤`ACTIVE_GAP_MS` rule so these shares match `regin stats time`. Longer
    stalls surface in the slowest-steps view instead.
    """
    if seg is not None:
        seg.engaged_ms += gap
    if gap > ACTIVE_GAP_MS:
        return
    if seg is not None:
        seg.active_ms += gap
    if event['name'] in CONTAINER_SPANS:
        return
    label = _activity_of(event['name'])
    acts[label] = acts.get(label, 0) + gap
    if seg is not None:
        bucket = seg_acts.setdefault(seg.index, {})
        bucket[label] = bucket.get(label, 0) + gap


def _count_edit(event: dict[str, Any], seg: Optional[Segment],
                files: dict[str, int]) -> None:
    path = event['attrs'].get('file_path')
    if path:
        files[path] = files.get(path, 0) + 1
    if seg is not None:
        seg.edits += 1


def _count_failure(event: dict[str, Any], seg: Optional[Segment],
                   fails: list[dict[str, Any]]) -> None:
    if seg is not None:
        seg.failures += 1
    detail = str(event['attrs'].get('error') or '').strip().splitlines()
    fails.append({
        'tool': event['attrs'].get('tool_name') or '?',
        'at': event['ts'].isoformat(timespec='seconds'),
        'detail': detail[0][:90] if detail else '',
    })


def _count_event(event: dict[str, Any], seg: Optional[Segment],
                 files: dict[str, int], fails: list[dict[str, Any]]) -> None:
    name = event['name']
    if name == 'tool.failure':
        _count_failure(event, seg, fails)
        return
    if seg is not None and name.startswith('tool.'):
        seg.tools += 1
    if name in _EDIT_NAMES:
        _count_edit(event, seg, files)


def _walk(timeline: list[dict[str, Any]]):
    """Single pass over the timeline collecting every postmortem view."""
    segments: list[Segment] = []
    acts: dict[str, int] = {}
    seg_acts: dict[int, dict[str, int]] = {}
    files: dict[str, int] = {}
    fails: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    seg: Optional[Segment] = None
    prev = None
    for event in timeline:
        if prev is not None:
            gap = int((event['ts'] - prev).total_seconds() * 1000)
            if 0 < gap <= ENGAGED_GAP_MS:
                _tally(event, gap, seg, acts, seg_acts)
                gaps.append({'ms': gap, 'label': _label_for(event)})
        if event['name'] == 'prompt':
            seg = _new_segment(event, len(segments))
            segments.append(seg)
        _count_event(event, seg, files, fails)
        prev = event['ts']
    for s in segments:
        bucket = seg_acts.get(s.index) or {}
        s.top_activity = max(bucket, key=bucket.get) if bucket else '-'
    return segments, acts, files, fails, gaps


def session_detail(trace_id: str) -> Optional[SessionDetail]:
    """Break one session down into where its time actually went.

    Segments are cut at each `prompt` span, so "which request ate the hour"
    is answerable directly. Gaps above `ENGAGED_GAP_MS` are dropped as
    away-from-keyboard rather than charged to the request that preceded them.
    """
    stats = load_sessions(_single_session_filter(trace_id))
    if not stats:
        return None
    segments, acts, files, fails, gaps = _walk(_fetch_timeline(trace_id))
    return SessionDetail(
        stat=stats[0],
        segments=segments,
        activity=sorted(({'activity': k, 'ms': v} for k, v in acts.items()),
                        key=lambda r: -r['ms']),
        files=sorted(({'file': k, 'edits': v} for k, v in files.items()),
                     key=lambda r: -r['edits']),
        slowest=sorted(gaps, key=lambda r: -r['ms'])[:SLOWEST_OPS],
        failures=fails,
    )


def resolve_trace_id(prefix: str) -> list[str]:
    """Trace ids matching `prefix`; exact match wins over prefix matches."""
    # 95 trace ids contain a literal `_`, a LIKE wildcard; without ESCAPE a
    # prefix silently matches unrelated sessions and reports them ambiguous.
    escaped = (prefix.replace('\\', '\\\\')
               .replace('%', '\\%').replace('_', '\\_'))
    rows = _query(
        "SELECT trace_id FROM sessions WHERE trace_id = :p "
        "OR trace_id LIKE :like ESCAPE '\\' ORDER BY last_seen DESC",
        {'p': prefix, 'like': f'{escaped}%'})
    ids = [r[0] for r in rows]
    return [prefix] if prefix in ids else ids

# --- diagnosis: what actually cost this session time ------------------------

REWORK_EDIT_MIN = 4       # edits to one file before it counts as a loop
REREAD_MIN = 3            # reads of one file before the extras are waste
REPEAT_CMD_MIN = 3        # identical shell commands before the extras are waste
_HUMAN_BLOCK_SPANS = ('permission.request', 'tool.AskUserQuestion')
_TEST_CMD = re.compile(
    r'\b(pytest|playwright\s+test|vitest|jest|npm\s+(run\s+)?test)\b', re.I)

# Narrowest lens first. Every gap is awarded to exactly one lens, so the
# findings plus the remainder add up to the session's attended time — no
# overlapping claims, no double counting, nothing silently unexplained.
# `rework-loop` therefore means "inter-edit time not already explained by a
# more specific pattern", and `ramp-up` is whatever is left before the first
# edit.
_CLAIM_ORDER = ('blocked-on-human', 'failure-retry', 'repeat-command',
                're-read', 'test-runs', 'rework-loop', 'ramp-up')


@dataclass
class Claim:
    """A lens's raw bid: which gaps it would explain, and how to describe it."""

    kind: str
    gaps: set
    headline: str
    detail: list = field(default_factory=list)


@dataclass
class Finding:
    """One named, costed reason the session ran long."""

    kind: str
    minutes: float
    headline: str
    detail: list = field(default_factory=list)

    def as_row(self) -> dict:
        return asdict(self)


@dataclass
class Timeline:
    """Events with the gap that produced each, and a running engaged clock.

    `clock[i]` is the engaged milliseconds elapsed up to event `i`, which makes
    a *window* ("how long did this file take to converge") cheap to measure:
    `clock[last] - clock[first]`. Gaps above `ENGAGED_GAP_MS` contribute 0, so
    an overnight break inside a window does not inflate it.
    """

    events: list
    gaps: list
    clock: list

    @property
    def total_ms(self) -> int:
        return self.clock[-1] if self.clock else 0

    def window_ms(self, first: int, last: int) -> int:
        return self.clock[last] - self.clock[first]

    def span(self, first: int, last: int) -> set:
        """Gap indices strictly inside the (first, last] event interval."""
        return set(range(first + 1, last + 1))


def build_timeline(spans: list) -> Timeline:
    gaps, clock, running = [], [], 0
    prev = None
    for event in spans:
        delta = 0
        if prev is not None:
            # round(), not int(): truncating biases every gap down by up to
            # 1 ms, which is ~0.44 ms/gap against the SQL ROUND() and drifts
            # seconds apart from `engaged_ms` over a few thousand spans.
            raw = round((event['ts'] - prev).total_seconds() * 1000)
            delta = raw if 0 < raw <= ENGAGED_GAP_MS else 0
        running += delta
        gaps.append(delta)
        clock.append(running)
        prev = event['ts']
    return Timeline(events=spans, gaps=gaps, clock=clock)


def _positions(tl: Timeline, names: tuple, key: str) -> dict:
    """Event indices grouped by (agent, attribute value).

    Scoped per agent because subagents run concurrently: two of them reading
    the same file, or editing different parts of it, is parallel work — not
    one agent churning. Cross-agent pairs were 301 of 23,430 edits (1%).
    """
    out: dict = {}
    for i, event in enumerate(tl.events):
        if event['name'] not in names:
            continue
        value = event['attrs'].get(key)
        if value:
            out.setdefault((event['agent_id'], str(value)), []).append(i)
    return out


def _short(key) -> str:
    path = key[1] if isinstance(key, tuple) else key
    return str(path).split('/')[-1]


def _edit_positions(tl: Timeline) -> list:
    """(index, (agent, file)) for every edit that names a file."""
    return [(i, (e['agent_id'], str(e['attrs'].get('file_path') or '')))
            for i, e in enumerate(tl.events)
            if e['name'] in _EDIT_NAMES and e['attrs'].get('file_path')]


def _claim_rework(tl: Timeline):
    """Files that needed several passes to converge.

    A correction's cost is the whole interval since the previous edit — the
    reading, thinking and testing that produced it — not the edit span, which
    is instantaneous. Every inter-edit interval is awarded once, so first-touch
    and corrective work partition the session's edit-production time.
    """
    edits = _edit_positions(tl)
    if len(edits) < 2:
        return None
    seen, gaps, counts = {edits[0][1]}, set(), {}
    for (prev_i, _), (i, path) in zip(edits, edits[1:]):
        if path in seen:
            gaps |= tl.span(prev_i, i)
            counts[path] = counts.get(path, 0) + 1
        seen.add(path)
    if not counts:
        return None
    worst = sorted(counts.items(), key=lambda kv: -kv[1])
    return Claim(
        kind='rework-loop', gaps=gaps,
        headline=(f'{sum(counts.values())} of {len(edits)} edits were '
                  f'corrections to a file already touched'),
        detail=[f'{_short(p)}: {n} corrective edits' for p, n in worst[:4]],
    )


def _claim_rereads(tl: Timeline):
    """Reads of a file the agent had already read — context lost and rebuilt."""
    gaps, files = set(), []
    for path, idx in _positions(tl, ('tool.Read',), 'file_path').items():
        if len(idx) < REREAD_MIN:
            continue
        gaps |= set(idx[1:])
        files.append((len(idx), path))
    if not files:
        return None
    files.sort(reverse=True)
    extras = sum(n - 1 for n, _ in files)
    return Claim(
        kind='re-read', gaps=gaps,
        headline=f'{extras} redundant re-reads across {len(files)} file(s)',
        detail=[f'{_short(p)} read {n}x' for n, p in files[:4]],
    )


def _claim_repeat_commands(tl: Timeline):
    gaps, cmds = set(), []
    for cmd, idx in _positions(tl, ('tool.Bash',), 'command_preview').items():
        if len(idx) < REPEAT_CMD_MIN:
            continue
        gaps |= set(idx[1:])
        cmds.append((len(idx), str(cmd[1]).splitlines()[0][:64]))
    if not cmds:
        return None
    cmds.sort(reverse=True)
    # Counts are per (agent, command): the same command run once by each of
    # ten subagents is fan-out, not one agent repeating itself.
    return Claim(
        kind='repeat-command', gaps=gaps,
        headline=f'{len(cmds)} command(s) re-run identically by one agent',
        detail=[f'{n}x  {c}' for n, c in cmds[:4]],
    )


def _claim_human_block(tl: Timeline):
    """The agent idle, waiting on an approval or an answer."""
    gaps, longest = set(), 0
    for i, event in enumerate(tl.events):
        if event['name'] not in _HUMAN_BLOCK_SPANS or i + 1 >= len(tl.gaps):
            continue
        gaps.add(i + 1)
        longest = max(longest, tl.gaps[i + 1])
    if not gaps:
        return None
    return Claim(
        kind='blocked-on-human', gaps=gaps,
        headline=f'Agent blocked on your approval/answer {len(gaps)}x',
        detail=[f'longest single wait {longest / 60_000:.1f} min'],
    )


def _streaks(tl: Timeline) -> list:
    """Runs of 3+ failures of one tool with no successful edit between them.

    A streak ends on a working edit rather than after an elapsed interval:
    two failures an hour apart with progress in between are two isolated
    errors, not a retry loop. Identifier-anchored, not clock-anchored.
    """
    out, tool, start, count, last = [], None, 0, 0, 0

    def close():
        if count >= 3 and tool is not None:
            out.append((start, last, count, tool))

    for i, event in enumerate(tl.events):
        if event['name'] in _EDIT_NAMES:
            close()
            tool, count = None, 0
            continue
        if event['name'] != 'tool.failure':
            continue
        this = str(event['attrs'].get('tool_name') or '?')
        if this != tool:
            close()
            tool, start, count = this, i, 0
        count += 1
        last = i
    close()
    return out


def _claim_failure_retries(tl: Timeline):
    streaks = _streaks(tl)
    if not streaks:
        return None
    gaps = set()
    for start, last, _, _ in streaks:
        gaps |= tl.span(start, last)
    ranked = sorted(streaks, key=lambda s: -(s[1] - s[0]))
    return Claim(
        kind='failure-retry', gaps=gaps,
        headline=f'{len(streaks)} failure streak(s) with no fix in between',
        detail=[f'{t} failed {n}x with no successful edit' for _, _, n, t in ranked[:3]],
    )


def _test_indices(tl: Timeline) -> set:
    out = set()
    for i, event in enumerate(tl.events):
        cmd = event['attrs'].get('command_preview')
        if event['name'] == 'tool.Bash' and cmd and _TEST_CMD.search(str(cmd)):
            out.add(i)
    return out


def _claim_test_runs(tl: Timeline):
    idx = _test_indices(tl)
    if len(idx) < 3:
        return None
    total = sum(tl.gaps[i] for i in idx)
    return Claim(
        kind='test-runs', gaps=idx,
        headline=f'{len(idx)} test runs',
        detail=[f'mean {total / len(idx) / 1000:.0f}s per run'],
    )


RAMPUP_MIN_MS = 600_000     # absolute floor: 10 min of orienting
RAMPUP_MIN_SHARE = 0.15     # …and it must be a real slice of the session


def _claim_rampup(tl: Timeline):
    """Everything before the first edit — orienting rather than building.

    Gated on both an absolute floor and a share of the session. A short
    look-around is how work starts, not waste; without the share gate this
    lens fired on 170 of 200 sessions and meant nothing.
    """
    for i, event in enumerate(tl.events):
        if event['name'] in _EDIT_NAMES:
            break
    else:
        return None
    cost = tl.clock[i]
    if cost < RAMPUP_MIN_MS or not tl.total_ms:
        return None
    if cost / tl.total_ms < RAMPUP_MIN_SHARE:
        return None
    return Claim(
        kind='ramp-up', gaps=set(range(0, i + 1)),
        headline='Orienting before the first edit',
        detail=[f'{i} events of searching/reading first, '
                f'{100 * cost / tl.total_ms:.0f}% of the session'],
    )


_CLAIMERS = (_claim_rework, _claim_rereads, _claim_repeat_commands,
             _claim_human_block, _claim_failure_retries, _claim_test_runs,
             _claim_rampup)


# How leftover time is labelled. Ordered: the first predicate that matches an
# event names the group its gap falls into.
_REMAINDER_GROUPS: tuple = (
    ('you typing / deciding', lambda e: e['name'] == 'prompt'),
    ('first-touch edits', lambda e: e['name'] in _EDIT_NAMES),
    ('model thinking', lambda e: e['name'] == 'assistant.thinking'),
    ('model responding', lambda e: e['name'] in ('assistant_response', 'turn')),
    ('one-off shell', lambda e: e['name'] == 'tool.Bash'),
    ('reading / searching', lambda e: e['name'] in (
        'tool.Read', 'tool.Glob', 'tool.Grep', 'tool.ToolSearch')),
    ('delegation overhead', lambda e: e['name'] in (
        'tool.Agent', 'subagent.start', 'subagent.stop')),
    ('harness overhead', lambda e: e['name'].startswith(
        ('harness.', 'hook.', 'rule.', 'instructions.', 'config.',
         'environment.', 'cwd.', 'permission.', 'session.', 'compact.'))),
)


def _remainder_group(event: dict) -> str:
    # Lane first: a subagent's Bash call is delegated work, not the main
    # thread's shell time. Without this, everything subagents did was
    # scattered across the main-thread groups and delegation looked like a
    # rounding error.
    if event['agent_id']:
        return 'subagent work'
    for label, matches in _REMAINDER_GROUPS:
        if matches(event):
            return label
    return 'other'


@dataclass
class Delegation:
    """How much of the session was done by subagents, and how parallel.

    Wall-clock attended time cannot show this: N subagents running at once
    still advance one shared clock, so their work is invisible in the merged
    timeline. Summing each lane's own gaps recovers the real agent-hours.
    """

    agents: int = 0
    main_ms: int = 0
    subagent_ms: int = 0
    wall_ms: int = 0

    @property
    def total_ms(self) -> int:
        return self.main_ms + self.subagent_ms

    @property
    def parallelism(self) -> float:
        return self.total_ms / self.wall_ms if self.wall_ms else 0.0

    @property
    def delegated_share(self) -> float:
        return self.subagent_ms / self.total_ms if self.total_ms else 0.0


def _lane_gap_ms(stamps: list) -> int:
    """Engaged milliseconds within one agent's own event stream."""
    total = 0
    for a, b in zip(stamps, stamps[1:]):
        gap = round((b - a).total_seconds() * 1000)
        if 0 < gap <= ENGAGED_GAP_MS:
            total += gap
    return total


def delegation(tl: Timeline) -> Delegation:
    lanes: dict = {}
    for event in tl.events:
        lanes.setdefault(event['agent_id'], []).append(event['ts'])
    main = _lane_gap_ms(sorted(lanes.get('', [])))
    sub = sum(_lane_gap_ms(sorted(v)) for k, v in lanes.items() if k)
    return Delegation(agents=len([k for k in lanes if k]), main_ms=main,
                      subagent_ms=sub, wall_ms=sum(tl.gaps))


_HUMAN_RESERVED = '(human)'


def _reserve_human_gaps(tl: Timeline) -> list:
    owner: list = [None] * len(tl.gaps)
    for i, event in enumerate(tl.events):
        if i < len(owner) and is_user_prompt(event):
            owner[i] = _HUMAN_RESERVED
    return owner


def _resolve_claims(tl: Timeline, claims: list) -> tuple:
    """Award each gap to exactly one lens, narrowest first.

    Gaps the human owns — the time spent typing a prompt — are reserved before
    any lens bids, so your keyboard time is never billed as agent waste. It
    resurfaces in the remainder instead, where it belongs.

    Returns (findings, owner) where `owner[i]` is the winning kind or None.
    """
    by_kind = {c.kind: c for c in claims if c is not None}
    owner = _reserve_human_gaps(tl)
    findings = []
    for kind in _CLAIM_ORDER:
        claim = by_kind.get(kind)
        if claim is None:
            continue
        won = [i for i in claim.gaps if i < len(owner) and owner[i] is None]
        for i in won:
            owner[i] = kind
        minutes = sum(tl.gaps[i] for i in won) / 60_000
        findings.append(Finding(kind=kind, minutes=minutes,
                                headline=claim.headline, detail=claim.detail))
    findings.sort(key=lambda f: -f.minutes)
    return findings, owner


def remainder_groups(tl: Timeline, owner: list) -> list:
    """Unclaimed time — the forward work no waste lens explains — grouped."""
    buckets: dict = {}
    for i, gap in enumerate(tl.gaps):
        if not gap or (owner[i] is not None and owner[i] != _HUMAN_RESERVED):
            continue
        label = _remainder_group(tl.events[i])
        buckets[label] = buckets.get(label, 0) + gap
    rows = [{'group': k, 'ms': v} for k, v in buckets.items()]
    return sorted(rows, key=lambda r: -r['ms'])


def analyse(trace_id: str) -> tuple:
    """(timeline, findings, remainder) for one session."""
    tl = build_timeline(_fetch_timeline(trace_id))
    claims = [claim(tl) for claim in _CLAIMERS]
    findings, owner = _resolve_claims(tl, claims)
    return tl, findings, remainder_groups(tl, owner)


def diagnose(trace_id: str) -> list:
    """Ranked, costed reasons a session ran long."""
    return analyse(trace_id)[1]


@dataclass
class Diagnosis:
    """Findings for one session, the leftover forward work, and a verdict."""

    stat: SessionStat
    findings: list
    remainder: list
    delegation: Delegation
    verdict: str
    attributed_ms: int
    remainder_ms: int
    attended_ms: int = 0
    blind_spots: list = field(default_factory=list)

    @property
    def attributed_share(self) -> float:
        return self.attributed_ms / self.attended_ms if self.attended_ms else 0.0


_VERDICT_BY_KIND = {
    'failure-retry': ('Stuck on a broken command: repeated failures with no '
                      'working edit in between.'),
    're-read': ('Context churn: the agent kept re-reading files it had already '
                'seen instead of retaining them.'),
    'repeat-command': ('Redundant re-running: the same command was issued over '
                       'and over.'),
    'blocked-on-human': ('Waiting on you: the agent idled on approvals and '
                         'questions.'),
    'ramp-up': 'Slow orientation: a long search phase before the first edit.',
    'test-runs': 'Test-bound: most of the time went into running the suite.',
}


def _blind_spots(stat: SessionStat) -> list:
    """Lenses this session's trace cannot support.

    Better to say the data is missing than to emit a confident verdict from a
    lens that silently saw nothing — Codex records edits as `tool.apply_patch`
    with no `file_path`, so rework is simply unanswerable there.
    """
    gaps = []
    if stat.edits and not stat.files_touched:
        gaps.append(f'rework and per-file churn — this harness '
                    f'({stat.agent_type or "unknown"}) records edits without a '
                    f'file path, so {stat.edits} edits cannot be attributed')
    if not stat.prompts:
        gaps.append('human vs agent split — no prompt spans recorded')
    return gaps


def _verdict(stat: SessionStat, findings: list, blind_spots: Sequence = ()) -> str:
    """Name the dominant failure mode in one sentence."""
    if blind_spots and not findings:
        return ('Cannot diagnose: the largest lenses are unavailable for this '
                'trace (see blind spots).')
    if not findings:
        return ('No recognised waste pattern — this session was mostly '
                'forward work.')
    human_share = stat.human_ms / stat.engaged_ms if stat.engaged_ms else 0
    rework_share = stat.rework_edits / stat.edits if stat.edits else 0
    if rework_share > 0.7:
        return (f'Convergence failure: {stat.rework_edits} of {stat.edits} '
                f'edits were corrections. The agent was guessing and checking, '
                f'not building.')
    # Median session is 8% human, p90 is 32% — 0.35 flags a genuine outlier
    # rather than the routine cost of being in the loop.
    if human_share > 0.35:
        return (f'You were the bottleneck: {human_share * 100:.0f}% of the '
                f'session was the agent waiting on your prompt or approval '
                f'(typical is 8%).')
    return _VERDICT_BY_KIND.get(
        findings[0].kind, f'Dominant cost: {findings[0].headline.lower()}.')


def diagnose_session(trace_id: str) -> Optional[Diagnosis]:
    """Full postmortem: what cost this session time, plus the forward-work
    remainder, so the two together account for all attended time."""
    stats = load_sessions(_single_session_filter(trace_id))
    if not stats:
        return None
    tl, findings, remainder = analyse(trace_id)
    blind = _blind_spots(stats[0])
    # Derived by subtraction, not by summing the findings' float minutes, so
    # the two sections are exactly the attended total with no rounding drift.
    remainder_ms = sum(r['ms'] for r in remainder)
    # attended_ms comes from the timeline this diagnosis actually walked, so
    # the two sections provably sum to the figure printed beside them.
    return Diagnosis(
        stat=stats[0], findings=findings, remainder=remainder,
        delegation=delegation(tl),
        verdict=_verdict(stats[0], findings, blind),
        attributed_ms=sum(tl.gaps) - remainder_ms,
        remainder_ms=remainder_ms, attended_ms=sum(tl.gaps),
        blind_spots=blind,
    )
