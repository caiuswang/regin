"""Serve-time cross-turn linking for `tool.ScheduleWakeup` spans.

A ScheduleWakeup call is always turn-final: the agent yields control at the end
of an autonomous (/loop) turn. This pass looks *across* the yield to stamp what
the agent did when it resumed — the first signal span that starts after the
wakeup — so the trace UI can render "paused … → resumed: polled task #abc"
instead of a bare wakeup row.

Order-based (next signal span by start_time), so it works on historical spans
that predate the capture builder and carry no delay/reason. Idempotent: mutates
each ScheduleWakeup span's `attributes` in place, deriving nothing it can't see
in the windowed span set (a wakeup at the window edge simply gets no link).
"""
from __future__ import annotations

# Spans that are not a meaningful "resume action": model reasoning, the recap
# text, and every harness.* internal fire between the wakeup and the real next
# step, so the scan skips them to reach what the agent actually *did*. All
# `harness.*` subtypes are noise (task_reminder / recap / local_command /
# tools_delta / skill_listing / …) — match the prefix, not a fixed list.
_RESUME_NOISE = frozenset({
    'assistant.thinking', 'assistant_response', 'turn', 'rule.check',
})

_WAKEUP = 'tool.ScheduleWakeup'


def _is_resume_noise(name: str) -> bool:
    return name in _RESUME_NOISE or name.startswith('harness.')


def _resume_action_label(resume: dict) -> str:
    """Short human label for the span a wakeup resumed into."""
    name = resume.get('name') or ''
    attrs = resume.get('attributes') if isinstance(resume.get('attributes'), dict) else {}
    if name == 'tool.TaskOutput':
        tid = attrs.get('task_id')
        return f'polled task #{tid}' if tid else 'polled task'
    if name == 'tool.Monitor':
        return 'polled monitor'
    if name == 'prompt':
        return 'resumed early (user)'
    if name == _WAKEUP:
        return 'rescheduled'
    if name == 'subagent.stop':
        return 'subagent finished'
    if name.startswith('tool.'):
        return name[5:]
    return name


def _attrs(span: dict) -> dict:
    a = span.get('attributes')
    if not isinstance(a, dict):
        a = {}
        span['attributes'] = a
    return a


def _stamp_resume(ordered: list[dict]) -> None:
    n = len(ordered)
    for i, span in enumerate(ordered):
        if span.get('name') != _WAKEUP:
            continue
        a = _attrs(span)
        if a.get('stop') is True:  # terminal wakeup — the loop ended, no resume
            continue
        resume = None
        for j in range(i + 1, n):
            if _is_resume_noise(ordered[j].get('name') or ''):
                continue
            resume = ordered[j]
            break
        if resume is None:
            continue
        a['resume_span_id'] = resume.get('span_id')
        a['resume_action'] = _resume_action_label(resume)


def _collapse_poll_loops(ordered: list[dict]) -> None:
    """Runs of consecutive wakeups that resume straight into another wakeup are
    an idle poll-loop (wake, nothing ready, reschedule). Stamp poll_round /
    poll_total on each member so the UI can show "waiting… (2/13)" progression
    instead of N identical rows."""
    wakeups = [s for s in ordered if s.get('name') == _WAKEUP]
    i = 0
    while i < len(wakeups):
        j = i
        # Chain while each wakeup resumes straight into the next — but never
        # across a terminal (stop) wakeup, which is not part of an idle loop.
        while (j + 1 < len(wakeups)
               and _attrs(wakeups[j + 1]).get('stop') is not True
               and _attrs(wakeups[j]).get('resume_span_id') == wakeups[j + 1].get('span_id')):
            j += 1
        run = wakeups[i:j + 1]
        if len(run) >= 2:
            for k, span in enumerate(run, 1):
                a = _attrs(span)
                a['poll_round'] = k
                a['poll_total'] = len(run)
        i = j + 1


def annotate_wakeup_resumes(spans: list[dict]) -> list[dict]:
    """Stamp cross-turn resume links onto every ScheduleWakeup span in `spans`.

    Mutates the span dicts in place (and returns the same list) so callers that
    share span object refs with a tree projection pick the annotations up for
    free. Safe on empty / wakeup-free lists."""
    if not spans:
        return spans
    # `merge_spans` may reorder, so sort here. On equal start_time (spans can
    # share a timestamp) fall back to the monotonic `session_spans` rowid, which
    # tracks arrival order for the append-only store — so a wakeup still sorts
    # before the span it resumed into.
    ordered = sorted(spans, key=lambda s: (s.get('start_time') or '', s.get('id') or 0))
    _stamp_resume(ordered)
    _collapse_poll_loops(ordered)
    return spans
