"""Serve-time demotion of stuck PENDING blockers (lib/trace/merge.py).

A PENDING `tool.*` / `permission.request` / ask placeholder that the
stale-blocker sweep can't reach (it is NEWER than the last prompt) is
demoted at read time to a resolved-interrupted rendering (status ERROR +
`is_interrupt`) when Claude demonstrably moved on OR the session is
inactive. The golden invariant: a legitimately-running long tool on an
ACTIVE session is never demoted.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from lib.trace.merge import merge_spans


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _row(span_id, name, attrs, *, status='UNSET', start=None, tid='t1', id=1):
    return {
        'id': id, 'trace_id': tid, 'span_id': span_id, 'parent_id': None,
        'name': name, 'kind': 'internal',
        'start_time': start or '2026-04-18T12:00:00',
        'end_time': None, 'duration_ms': 0,
        'status_code': status, 'status_message': None,
        'attributes': attrs, 'turn_uuid': None,
    }


def _served(rows, **kw):
    return {s['span_id']: s for s in merge_spans(rows, **kw)}


# ── rule (a): same-agent moved on ────────────────────────────────

def test_demote_when_same_agent_moved_on():
    """A main PENDING tool with a later main assistant_response PAST THE GRACE
    → demoted (Claude produced more output, so the tool never resolved). The
    gap must exceed `_MOVED_ON_GRACE_SEC` (90s) so the backfill has had its
    shot at superseding a merely-lost PostToolUse first."""
    rows = [
        _row('pending-tu1', 'tool.Bash', {'tool_use_id': 'tu1'},
             status='PENDING', start='2026-04-18T12:00:00', id=1),
        _row('resp-1', 'assistant_response', {'text': 'moved on'},
             start='2026-04-18T12:03:00', id=2),
    ]
    served = _served(rows)
    demoted = served['pending-tu1']
    assert demoted['status_code'] == 'ERROR'
    assert demoted['attributes']['is_interrupt'] is True
    assert demoted['attributes']['interrupt_source'] == 'stale'


def test_no_demote_when_same_agent_moved_on_within_grace():
    """A same-agent response only 10s after the pending must NOT demote it: the
    grace window protects a merely-lost PostToolUse from a transient
    '⏹ interrupted' mislabel before the 60s transcript backfill can supersede
    it with the true OK span."""
    rows = [
        _row('pending-tu1', 'tool.Bash', {'tool_use_id': 'tu1'},
             status='PENDING', start='2026-04-18T12:00:00', id=1),
        _row('resp-1', 'assistant_response', {'text': 'moved on'},
             start='2026-04-18T12:00:10', id=2),
    ]
    served = _served(rows)  # no session_activity → active
    assert served['pending-tu1']['status_code'] == 'PENDING'
    assert 'is_interrupt' not in served['pending-tu1']['attributes']


def test_moved_on_only_counts_same_agent():
    """A SUBAGENT's later assistant_response must not demote a MAIN pending —
    the main tool may still be legitimately running."""
    rows = [
        _row('pending-tu1', 'tool.Bash', {'tool_use_id': 'tu1'},
             status='PENDING', start='2026-04-18T12:00:00', id=1),
        _row('resp-sub', 'assistant_response',
             {'text': 'subagent output', 'agent_id': 'a1'},
             start='2026-04-18T12:03:00', id=2),  # past grace, but different agent
    ]
    served = _served(rows)  # no session_activity → active
    assert served['pending-tu1']['status_code'] == 'PENDING'


# ── rule (b): session inactive + old pending ─────────────────────

def test_demote_when_session_ended_and_old():
    now = datetime(2026, 4, 18, 13, 0, 0)
    old = now - timedelta(seconds=120)
    rows = [_row('pending-tu2', 'tool.Bash', {'tool_use_id': 'tu2'},
                 status='PENDING', start=_iso(old), id=1)]
    served = _served(rows, session_activity={'status': 'ended', 'now': now})
    assert served['pending-tu2']['status_code'] == 'ERROR'
    assert served['pending-tu2']['attributes']['interrupt_source'] == 'stale'


def test_demote_when_session_stale_and_old():
    now = datetime(2026, 4, 18, 13, 0, 0)
    old = now - timedelta(seconds=200)
    last_seen = now - timedelta(seconds=1200)  # > INACTIVE_THRESHOLD
    rows = [_row('permreq-tu3', 'permission.request', {'tool_use_id': 'tu3'},
                 status='PENDING', start=_iso(old), id=1)]
    served = _served(rows, session_activity={
        'status': 'active', 'last_seen': _iso(last_seen), 'now': now})
    assert served['permreq-tu3']['status_code'] == 'ERROR'


def test_no_demote_when_inactive_but_pending_too_fresh():
    """An ended session whose pending only just started (< 60s) is not yet
    demoted — the guard against calling a barely-started tool dead."""
    now = datetime(2026, 4, 18, 13, 0, 0)
    fresh = now - timedelta(seconds=10)
    rows = [_row('pending-tu4', 'tool.Bash', {'tool_use_id': 'tu4'},
                 status='PENDING', start=_iso(fresh), id=1)]
    served = _served(rows, session_activity={'status': 'ended', 'now': now})
    assert served['pending-tu4']['status_code'] == 'PENDING'


# ── golden invariant: never demote a live long tool ──────────────

def test_never_demote_running_long_tool_on_active_session():
    """A long-running tool (started 5000s ago) on an ACTIVE session with NO
    same-agent completion activity after it must stay PENDING."""
    now = datetime(2026, 4, 18, 13, 0, 0)
    started = now - timedelta(seconds=5000)
    last_seen = now - timedelta(seconds=3)  # session actively advancing
    rows = [_row('pending-tu5', 'tool.Bash', {'tool_use_id': 'tu5'},
                 status='PENDING', start=_iso(started), id=1)]
    served = _served(rows, session_activity={
        'status': 'active', 'last_seen': _iso(last_seen), 'now': now})
    assert served['pending-tu5']['status_code'] == 'PENDING'
    assert 'is_interrupt' not in served['pending-tu5']['attributes']


def test_demotion_does_not_mutate_input():
    rows = [
        _row('pending-tu6', 'tool.Bash', {'tool_use_id': 'tu6'},
             status='PENDING', start='2026-04-18T12:00:00', id=1),
        _row('resp-2', 'assistant_response', {'text': 'x'},
             start='2026-04-18T12:03:00', id=2),
    ]
    merge_spans(rows)
    assert rows[0]['status_code'] == 'PENDING'
    assert 'is_interrupt' not in rows[0]['attributes']
