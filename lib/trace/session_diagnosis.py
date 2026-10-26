"""Single-session diagnosis: award every attended gap to exactly one cost
pattern (rework, re-reads, blocked-on-human, ...) and rank the findings."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional, Sequence

from lib.trace.session_time import (
    ENGAGED_GAP_MS,
    SessionStat,
    _EDIT_NAMES,
    load_sessions,
)
from lib.trace.session_detail import (
    _fetch_timeline,
    _single_session_filter,
    is_user_prompt,
)


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
    usd: Optional[float] = None

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


def _resume_rewrites(tl: Timeline) -> list:
    """Resumes that Claude Code itself flagged as rewriting a cold cache."""
    return [e['attrs'] for e in tl.events
            if e['name'] == 'session.start'
            and e['attrs'].get('prompt_cache_likely_expired') is True
            and _positive_number(e['attrs'].get('estimated_cache_write_usd'))]


def _positive_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value > 0)


def _finding_resume_cache(tl: Timeline) -> Optional[Finding]:
    """Dollar cost of re-writing the prompt cache on resume.

    The figure is Claude Code's own estimate from the SessionStart payload,
    not a regin reconstruction. It is a money finding, not a time one, so it
    claims no gaps and the findings + remainder partition of attended time
    is untouched.
    """
    starts = _resume_rewrites(tl)
    if not starts:
        return None
    usd = sum(float(a['estimated_cache_write_usd']) for a in starts)
    detail = []
    for a in starts:
        idle = a.get('seconds_since_last_response')
        idle_txt = (f'{float(idle) / 60:.0f} min after the last response '
                    f'(seconds_since_last_response={idle})'
                    if idle is not None else 'idle time not reported')
        ctx = a.get('context_tokens')
        ctx_txt = f', {int(ctx):,} context tokens' if ctx is not None else ''
        detail.append(f'{a.get("source") or "resume"} '
                      f'{idle_txt}{ctx_txt}: '
                      f'${float(a["estimated_cache_write_usd"]):.2f}')
    return Finding(
        kind='resume-cache-rewrite', minutes=0.0, usd=usd,
        headline=(f'Resume re-wrote an expired prompt cache: ${usd:.2f} '
                  f'across {len(starts)} resume(s)'),
        detail=detail,
    )


def analyse(trace_id: str) -> tuple:
    """(timeline, findings, remainder) for one session."""
    tl = build_timeline(_fetch_timeline(trace_id))
    claims = [claim(tl) for claim in _CLAIMERS]
    findings, owner = _resolve_claims(tl, claims)
    resume = _finding_resume_cache(tl)
    if resume is not None:
        findings.append(resume)
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
        verdict=_verdict(stats[0], [f for f in findings if f.usd is None],
                         blind),
        attributed_ms=sum(tl.gaps) - remainder_ms,
        remainder_ms=remainder_ms, attended_ms=sum(tl.gaps),
        blind_spots=blind,
    )
