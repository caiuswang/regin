"""Single-session timeline: the per-segment breakdown behind `stats session`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from lib.trace.pending_spans import parse_naive_ts
from lib.trace.session_time import (
    ACTIVE_GAP_MS,
    CONTAINER_SPANS,
    ENGAGED_GAP_MS,
    SessionStat,
    StatsFilter,
    _EDIT_NAMES,
    _activity_of,
    _live_filter,
    _query,
    load_sessions,
)


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
