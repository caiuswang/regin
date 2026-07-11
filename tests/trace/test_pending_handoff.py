"""Placeholder → resolved handoff, post-refactor.

The store is now APPEND-ONLY: ingest never deletes or promotes rows, so a
live PENDING placeholder and its resolved counterpart coexist in
`session_spans`. All dedup/supersession moved to the pure serve-time
`merge_spans` (lib/trace/merge.py), which drops the superseded placeholder
at read time. These tests cover both halves:

  * ingest stays append-only and counts pending placeholders toward no
    aggregate (reserved id prefixes are skipped);
  * `merge_spans` is the golden spec for which row wins when a placeholder
    and its resolution are both present in a window.
"""

from __future__ import annotations

import sqlite3

import pytest

from lib.trace.merge import merge_spans
from lib.trace.pending_spans import (
    perm_pending_id,
    prompt_placeholder_id,
    tool_pending_id,
)


# ── merge_spans: pure dedup spec (no DB) ─────────────────────────

def _row(span_id, name, attrs, *, status='UNSET', tid='t1', id=1, parent_id=None):
    return {
        'id': id, 'trace_id': tid, 'span_id': span_id, 'parent_id': parent_id,
        'name': name, 'kind': 'internal',
        'start_time': '2026-04-18T12:00:00', 'end_time': '2026-04-18T12:00:00',
        'duration_ms': 0, 'status_code': status, 'status_message': None,
        'attributes': attrs, 'turn_uuid': None,
    }


def _survivors(rows):
    return {s['span_id'] for s in merge_spans(rows)}


def test_merge_anchor_supersedes_prompt_placeholder():
    pid = prompt_placeholder_id('t1', 'hello world')
    rows = [
        _row(pid, 'prompt', {'text': 'hello world', 'live_placeholder': True},
             status='PENDING', id=1),
        _row('prompt-abcdef0123456', 'prompt', {'text': 'hello world'}, id=2),
    ]
    assert _survivors(rows) == {'prompt-abcdef0123456'}


def test_merge_keeps_in_flight_placeholder_with_no_anchor_yet():
    """A placeholder whose real anchor hasn't landed survives — that's the
    in-flight prompt the live view must still show."""
    pid = prompt_placeholder_id('t1', 'still typing')
    rows = [_row(pid, 'prompt', {'text': 'still typing'}, status='PENDING', id=1)]
    assert _survivors(rows) == {pid}


def test_merge_pending_tool_superseded_by_resolved_tool():
    tu = 'toolu_abc123456789'
    rows = [
        _row(tool_pending_id(tu), 'tool.AskUserQuestion',
             {'tool_use_id': tu, 'live': True}, status='PENDING', id=1),
        _row('rand16hexabcdef0', 'tool.AskUserQuestion',
             {'tool_use_id': tu, 'answers': {'q': 'A'}}, id=2),
    ]
    assert _survivors(rows) == {'rand16hexabcdef0'}


def test_merge_pending_tool_superseded_by_deny_synth():
    tu = 'toolu_deny99999999'
    rows = [
        _row(tool_pending_id(tu), 'tool.AskUserQuestion', {'tool_use_id': tu},
             status='PENDING', id=1),
        _row(f'askdeny-{tu[:13]}', 'tool.AskUserQuestion',
             {'tool_use_id': tu, 'denied': True}, status='ERROR', id=2),
    ]
    assert tool_pending_id(tu) not in _survivors(rows)


def test_merge_permreq_superseded_by_granting_tool_via_tool_use_id():
    tu = 'toolu_grant1234567'
    rows = [
        _row(perm_pending_id(tu), 'permission.request', {'tool_use_id': tu},
             status='PENDING', id=1),
        _row('rand9', 'tool.Bash', {'tool_use_id': tu}, id=2),
    ]
    assert perm_pending_id(tu) not in _survivors(rows)


def test_merge_no_false_supersession_on_different_tool_use_id():
    pend = tool_pending_id('toolu_keepme1234567')
    rows = [
        _row(pend, 'tool.AskUserQuestion', {'tool_use_id': 'toolu_keepme1234567'},
             status='PENDING', id=1),
        _row('rand', 'tool.AskUserQuestion', {'tool_use_id': 'toolu_other7654321'}, id=2),
    ]
    assert pend in _survivors(rows)


def test_merge_permreq_without_tool_use_id_dropped_by_tool_name():
    """Claude Code's PermissionRequest payload has no tool_use_id, so the
    placeholder lands with a random id. It's correlated by tool_name when the
    granted tool resolves."""
    rows = [
        _row('rand-perm-1', 'permission.request', {'tool_name': 'Bash'},
             status='PENDING', id=1),
        _row('rand-bash-1', 'tool.Bash', {'tool_name': 'Bash', 'tool_use_id': 'toolu_x'}, id=2),
    ]
    assert 'rand-perm-1' not in _survivors(rows)


def test_merge_permreq_dropped_by_tool_name_from_span_name_fallback():
    rows = [
        _row('rand-perm-2', 'permission.request', {'tool_name': 'AskUserQuestion'},
             status='PENDING', id=1),
        _row('rand-auq', 'tool.AskUserQuestion', {}, id=2),  # no tool_name attr
    ]
    assert 'rand-perm-2' not in _survivors(rows)


def test_merge_permreq_dropped_by_permission_denied():
    rows = [
        _row('rand-perm-3', 'permission.request', {'tool_name': 'Edit'},
             status='PENDING', id=1),
        _row('deny-3', 'permission.denied', {'tool_name': 'Edit'}, status='ERROR', id=2),
    ]
    assert 'rand-perm-3' not in _survivors(rows)


def test_merge_permreq_kept_for_different_tool_name():
    rows = [
        _row('rand-perm-4', 'permission.request', {'tool_name': 'Bash'},
             status='PENDING', id=1),
        _row('rand-read', 'tool.Read', {'tool_name': 'Read'}, id=2),
    ]
    assert 'rand-perm-4' in _survivors(rows)


def test_merge_lone_pending_permission_not_self_dropped():
    rows = [_row('rand-perm-5', 'permission.request', {'tool_name': 'Bash'},
                 status='PENDING', id=1)]
    assert 'rand-perm-5' in _survivors(rows)


def test_merge_stale_pending_tool_dropped_by_later_prompt():
    """An interrupted AskUserQuestion/ExitPlanMode pending (never resolved) is
    dropped once a higher-id prompt lands — it blocked the session, so a new
    prompt means it's stale."""
    rows = [
        _row('pending-toolu_intr', 'tool.AskUserQuestion', {'tool_use_id': 'toolu_intr'},
             status='PENDING', id=1),
        _row('prompt-next00000', 'prompt', {'text': 'next'}, id=2),
    ]
    assert 'pending-toolu_intr' not in _survivors(rows)


def test_merge_stale_pending_permission_dropped_by_later_prompt():
    rows = [
        _row('rand-perm-stale', 'permission.request', {'tool_name': 'Bash'},
             status='PENDING', id=1),
        _row(prompt_placeholder_id('t1', 'hi'), 'prompt', {'text': 'hi'},
             status='PENDING', id=2),
    ]
    assert 'rand-perm-stale' not in _survivors(rows)


def test_merge_keeps_current_turn_pending_tool():
    """A pending tool created AFTER the latest prompt (higher id) is the active
    turn's own blocker and must survive."""
    rows = [
        _row('prompt-cur000000', 'prompt', {'text': 'current'}, id=1),
        _row('pending-toolu_cur', 'tool.AskUserQuestion', {'tool_use_id': 'toolu_cur'},
             status='PENDING', id=2),
    ]
    assert 'pending-toolu_cur' in _survivors(rows)


def test_merge_keeps_pending_tool_when_no_prompt_present():
    rows = [
        _row('pending-toolu_keep', 'tool.AskUserQuestion', {'tool_use_id': 'toolu_keep'},
             status='PENDING', id=1),
        _row('rand-bash', 'tool.Bash', {'tool_use_id': 'toolu_other'}, id=2),
    ]
    assert 'pending-toolu_keep' in _survivors(rows)


def test_merge_drops_stray_prompt_placeholder_below_newer_prompt():
    """A client-only command (/workflows) leaves a `promptlive-` prompt
    placeholder that no model turn backs. A later prompt makes it stale —
    subsumes the old reconcile_prompt_spans deletion."""
    rows = [
        _row('promptlive-workflows1', 'prompt', {'text': '/workflows'},
             status='PENDING', id=1),
        _row('prompt-realnext00000', 'prompt', {'text': 'real next'}, id=2),
    ]
    sv = _survivors(rows)
    assert 'promptlive-workflows1' not in sv
    assert 'prompt-realnext00000' in sv


def test_merge_slash_command_expansion_transferred_to_resolved_anchor():
    """A slash command (`/goal-verified`) yields TWO prompt rows: a resolved
    `prompt-<uuid>` anchor carrying only the 14-char `/command` echo, and a
    PENDING `promptlive-` placeholder carrying the full expansion. A later
    turn's prompt would otherwise make the placeholder stale and drop the
    expansion. Instead exactly the resolved anchor survives for turn 1, status
    OK, now carrying the FULL expansion text."""
    expansion = '/goal-verified # Roadmap — refactor the current session ' + 'x' * 2000
    ph = prompt_placeholder_id('t1', expansion)
    rows = [
        _row(ph, 'prompt', {'text': expansion, 'live_placeholder': True},
             status='PENDING', id=1),
        _row('prompt-35cecf24cf68', 'prompt', {'text': '/goal-verified'},
             status='OK', id=2),
        _row('prompt-next00000000', 'prompt', {'text': 'merge it back to master'},
             status='OK', id=3),
    ]
    merged = merge_spans(rows)
    turn1 = [s for s in merged if s['span_id'] == 'prompt-35cecf24cf68']
    # the placeholder is gone; the resolved anchor is the sole turn-1 prompt
    assert ph not in {s['span_id'] for s in merged}
    assert len(turn1) == 1
    survivor = turn1[0]
    assert survivor['status_code'] == 'OK'
    assert survivor['attributes']['text'] == expansion
    assert len(survivor['attributes']['text']) > len('/goal-verified')


def test_merge_truncated_prompt_anchor_absorbs_full_placeholder_text():
    """A large prompt (e.g. an external-agent regenerate task) yields a resolved
    `prompt-<uuid>` anchor whose text was byte-capped with the `\n…[truncated]`
    marker at post time, plus a PENDING `promptlive-` placeholder holding the
    untruncated prompt. The anchor's first 512 chars hash identically to the
    placeholder (same prefix), so the supersession sweep would otherwise drop
    the placeholder and strand the 8 KiB view. Instead the anchor survives
    carrying the FULL text, marker gone."""
    full = '# Regin Topic Proposal Agent Task\n\n' + 'y' * 20000
    head = full[:8182]                       # the byte-capped prefix
    anchor_text = head + '\n…[truncated]'     # _PROMPT_ANCHOR_TRUNC_MARKER
    ph = prompt_placeholder_id('t1', full)
    rows = [
        _row(ph, 'prompt', {'text': full, 'live_placeholder': True},
             status='PENDING', id=1),
        _row('prompt-cafebabe0001', 'prompt', {'text': anchor_text, 'chars': len(full)},
             status='OK', id=2),
        _row('prompt-next00000000', 'prompt', {'text': 'a later prompt'},
             status='OK', id=3),
    ]
    merged = {s['span_id']: s for s in merge_spans(rows)}
    assert ph not in merged                       # placeholder absorbed + dropped
    survivor = merged['prompt-cafebabe0001']
    assert survivor['status_code'] == 'OK'
    assert survivor['attributes']['text'] == full     # full text, not the 8 KiB head
    assert not survivor['attributes']['text'].endswith('…[truncated]')


def test_merge_untruncated_prompt_placeholder_drops_normally():
    """A normal short prompt (anchor == placeholder text, no marker) is NOT an
    expansion anchor: the placeholder is dropped by the supersession sweep as
    before and no absorb copy is made."""
    text = 'just a short prompt'
    ph = prompt_placeholder_id('t1', text)
    rows = [
        _row(ph, 'prompt', {'text': text}, status='PENDING', id=1),
        _row('prompt-deadbeef0001', 'prompt', {'text': text}, status='OK', id=2),
        _row('prompt-next00000000', 'prompt', {'text': 'later'}, status='OK', id=3),
    ]
    merged = {s['span_id']: s for s in merge_spans(rows)}
    assert ph not in merged
    assert merged['prompt-deadbeef0001']['attributes']['text'] == text


def test_merge_two_distinct_slash_command_turns_do_not_cross_wire():
    """Two separate `/goal-verified` turns each pair their placeholder with
    their OWN nearest resolved anchor (by id), not the other turn's."""
    exp_a = '/goal-verified ' + 'A' * 100
    exp_b = '/goal-verified ' + 'B' * 200
    ph_a = prompt_placeholder_id('t1', exp_a)
    ph_b = prompt_placeholder_id('t1', exp_b)
    rows = [
        _row(ph_a, 'prompt', {'text': exp_a}, status='PENDING', id=1),
        _row('prompt-aaaaaaaaaaaa', 'prompt', {'text': '/goal-verified'},
             status='OK', id=2),
        _row(ph_b, 'prompt', {'text': exp_b}, status='PENDING', id=3),
        _row('prompt-bbbbbbbbbbbb', 'prompt', {'text': '/goal-verified'},
             status='OK', id=4),
        _row('prompt-laterzzzzzzz', 'prompt', {'text': 'later'}, status='OK', id=5),
    ]
    merged = {s['span_id']: s for s in merge_spans(rows)}
    assert ph_a not in merged and ph_b not in merged
    assert merged['prompt-aaaaaaaaaaaa']['attributes']['text'] == exp_a
    assert merged['prompt-bbbbbbbbbbbb']['attributes']['text'] == exp_b


def test_merge_two_placeholders_before_their_anchors_pair_one_to_one():
    """When BOTH placeholders are ingested before BOTH resolved anchors
    (id order ph_a, ph_b, anchor_a, anchor_b — a batch of two slash commands),
    each placeholder must still claim its OWN anchor. Without one-to-one
    claiming both placeholders would grab the earliest anchor and one expansion
    would be lost."""
    exp_a = '/goal-verified ' + 'A' * 100
    exp_b = '/goal-verified ' + 'B' * 200
    ph_a = prompt_placeholder_id('t1', exp_a)
    ph_b = prompt_placeholder_id('t1', exp_b)
    rows = [
        _row(ph_a, 'prompt', {'text': exp_a}, status='PENDING', id=1),
        _row(ph_b, 'prompt', {'text': exp_b}, status='PENDING', id=2),
        _row('prompt-aaaaaaaaaaaa', 'prompt', {'text': '/goal-verified'},
             status='OK', id=3),
        _row('prompt-bbbbbbbbbbbb', 'prompt', {'text': '/goal-verified'},
             status='OK', id=4),
        _row('prompt-laterzzzzzzz', 'prompt', {'text': 'later'}, status='OK', id=5),
    ]
    merged = {s['span_id']: s for s in merge_spans(rows)}
    assert ph_a not in merged and ph_b not in merged
    # earliest placeholder → earliest anchor; no cross-wire, no lost expansion
    assert merged['prompt-aaaaaaaaaaaa']['attributes']['text'] == exp_a
    assert merged['prompt-bbbbbbbbbbbb']['attributes']['text'] == exp_b


def test_merge_keeps_stray_prompt_placeholder_as_newest():
    """The newest prompt (no later prompt) is kept — it may be a genuine
    in-flight prompt, mirroring reconcile_prompt_spans' 'never delete the
    newest' guard."""
    rows = [_row('promptlive-current0001', 'prompt', {'text': '/workflows'},
                 status='PENDING', id=1)]
    assert 'promptlive-current0001' in _survivors(rows)


def test_merge_ceiling_drops_stray_prompt_newest_in_older_window():
    """In a scroll-up window the stray can be the newest prompt *in the
    window* — the global prompt_id_ceiling still marks it stale so it can't
    resurface mid-history."""
    rows = [_row('promptlive-stray00001', 'prompt', {'text': '/workflows'},
                 status='PENDING', id=3)]
    # window-local max is the stray itself (kept), but a later prompt exists
    # globally (ceiling=9) → dropped.
    assert {s['span_id'] for s in merge_spans(rows, prompt_id_ceiling=9)} == set()
    # no ceiling (whole-session read) → it's genuinely newest → kept.
    assert {s['span_id'] for s in merge_spans(rows)} == {'promptlive-stray00001'}


# ── ingest: append-only + counter discipline (DB) ────────────────

@pytest.fixture
def trace_db(tmp_path, monkeypatch):
    db_path = tmp_path / 'trace.db'
    import lib.orm.engine as db_module
    monkeypatch.setattr(db_module, 'DB_PATH', str(db_path))
    db_module.init_db()
    return str(db_path)


def _span(span_id, name, attrs, *, status='UNSET', tid='t1',
          start='2026-04-18T12:00:00'):
    span = {
        'trace_id': tid, 'span_id': span_id, 'name': name,
        'parent_id': None, 'kind': 'internal',
        'start_time': start, 'end_time': start,
        'duration_ms': 0, 'status_code': status, 'status_message': None,
        'attributes': attrs,
    }
    return (span, attrs)


def _ingest(rows):
    from lib.trace.trace_service import ingest_session_spans
    ingest_session_spans(rows)


def _exists(db, table, span_id, tid='t1'):
    conn = sqlite3.connect(db)
    try:
        n = conn.execute(
            f'SELECT COUNT(*) FROM {table} WHERE trace_id=? AND span_id=?',
            (tid, span_id),
        ).fetchone()[0]
        return n > 0
    finally:
        conn.close()


def _session_counter(db, col, tid='t1'):
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            f'SELECT {col} FROM sessions WHERE trace_id=?', (tid,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _dbid(db, table, span_id, tid='t1'):
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            f'SELECT id FROM {table} WHERE trace_id=? AND span_id=?',
            (tid, span_id),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_placeholder_and_anchor_coexist_append_only(trace_db):
    """Append-only: the placeholder is NOT deleted when the real anchor lands —
    both rows persist; the merge drops the placeholder at read time. Only the
    real anchor advances the prompts counter (the placeholder's reserved id
    prefix is skipped)."""
    pid = prompt_placeholder_id('t1', 'hello world')
    _ingest([_span(pid, 'prompt', {'text': 'hello world', 'live_placeholder': True},
                   status='PENDING')])
    ph_dbid = _dbid(trace_db, 'session_spans', pid)
    assert ph_dbid is not None

    _ingest([_span('prompt-abcdef0123456', 'prompt', {'text': 'hello world'})])
    # both rows still present in BOTH tables (no deletion, no promotion)
    assert _exists(trace_db, 'session_spans', pid)
    assert _exists(trace_db, 'session_spans', 'prompt-abcdef0123456')
    assert _exists(trace_db, 'session_trace_map', pid)
    assert _exists(trace_db, 'session_trace_map', 'prompt-abcdef0123456')
    # placeholder keeps its own row id (append-only, no in-place rewrite)
    assert _dbid(trace_db, 'session_spans', pid) == ph_dbid
    # only the real anchor counted toward prompts
    assert _session_counter(trace_db, 'prompts') == 1


def test_anchor_without_placeholder_inserts_normally(trace_db):
    _ingest([_span('prompt-noplaceholder', 'prompt', {'text': 'fresh prompt'})])
    assert _exists(trace_db, 'session_spans', 'prompt-noplaceholder')
    assert _exists(trace_db, 'session_trace_map', 'prompt-noplaceholder')


def test_anchor_reingest_does_not_duplicate(trace_db):
    """Re-ingesting an already-present anchor upserts in place by
    (trace_id, span_id) — no second row."""
    _ingest([_span('prompt-reingest0000', 'prompt', {'text': 'reingest me'})])
    _ingest([_span('prompt-reingest0000', 'prompt', {'text': 'reingest me'})])
    conn = sqlite3.connect(trace_db)
    n = conn.execute(
        "SELECT COUNT(*) FROM session_spans WHERE span_id='prompt-reingest0000'"
    ).fetchone()[0]
    conn.close()
    assert n == 1


def test_pending_placeholder_does_not_advance_counters(trace_db):
    pid = prompt_placeholder_id('t1', 'solo')
    _ingest([_span(pid, 'prompt', {'text': 'solo'}, status='PENDING')])
    assert _session_counter(trace_db, 'prompts') in (None, 0)


def test_paginated_older_window_drops_stray_prompt(trace_db):
    """Integration: a stray `/workflows` prompt placeholder does NOT resurface
    in a scroll-up (before_id) window where it's the newest anchor — the global
    prompt_id_ceiling threaded by fetch_session_paginated drops it. Guards the
    window-local-max gap that would otherwise leak the stray mid-history."""
    from lib.trace.trace_service.queries import fetch_session_paginated
    _ingest([_span('promptlive-strayold', 'prompt', {'text': '/workflows'},
                   status='PENDING', start='2026-04-18T12:00:00')])
    _ingest([_span('prompt-realnewer00', 'prompt', {'text': 'real'},
                   start='2026-04-18T12:00:05')])
    real_id = _dbid(trace_db, 'session_spans', 'prompt-realnewer00')
    # Scroll-up window: anchors older than the real prompt → only the stray.
    widened, _tree, _more, _ = fetch_session_paginated('t1', limit=10, before_id=real_id)
    assert 'promptlive-strayold' not in {s['span_id'] for s in widened}
