"""A prompt row supersedes PENDING blockers at its turn's birth id.

kimi's transcript tail can write a turn's resolved prompt anchor AFTER that
turn's first PreToolUse placeholder. Keyed on the anchor's own id, the
stale-blocker sweep read the turn's own echo as a newer prompt and hid the
call still in flight — a session parked inside an MCP OAuth login showed a
prompt and nothing else.
"""

from __future__ import annotations

import json
import sqlite3

from lib.trace.merge import merge_spans
from lib.trace.pending_spans import prompt_placeholder_id, tool_pending_id
from lib.trace.trace_service.queries import _prompt_ceiling

TID = 'session_birth'


def _row(span_id, name, attrs, *, status='OK', id):
    return {
        'id': id, 'trace_id': TID, 'span_id': span_id, 'parent_id': None,
        'name': name, 'kind': 'internal',
        'start_time': '2026-10-08T04:30:00', 'end_time': '2026-10-08T04:30:00',
        'duration_ms': 0, 'status_code': status, 'status_message': None,
        'attributes': attrs, 'turn_uuid': None,
    }


def _placeholder(text, id):
    return _row(prompt_placeholder_id(TID, text), 'prompt',
                {'text': text, 'live_placeholder': True},
                status='PENDING', id=id)


def _pending_tool(tu, id):
    return _row(tool_pending_id(tu), 'tool.mcp__linear__authenticate',
                {'tool_name': 'mcp__linear__authenticate', 'tool_use_id': tu,
                 'live': True}, status='PENDING', id=id)


def _survivors(rows, **kw):
    return {s['span_id'] for s in merge_spans(rows, **kw)}


def test_in_flight_tool_survives_its_own_late_transcript_anchor():
    rows = [
        _placeholder('fix defect', 1),
        _pending_tool('tu-1', 2),
        _row('prompt-kprompt-0', 'prompt', {'text': 'fix defect'}, id=3),
    ]
    assert tool_pending_id('tu-1') in _survivors(rows)


def test_a_later_turn_still_retires_a_prior_turns_pending_tool():
    rows = [
        _placeholder('first', 1),
        _row('prompt-kprompt-0', 'prompt', {'text': 'first'}, id=2),
        _pending_tool('tu-1', 3),
        _placeholder('second', 4),
        _row('prompt-kprompt-1', 'prompt', {'text': 'second'}, id=5),
    ]
    assert tool_pending_id('tu-1') not in _survivors(rows)


def test_a_repeated_prompt_does_not_borrow_the_first_turns_birth():
    """Same text → same placeholder id: the second turn writes no row of its
    own, and the first turn's placeholder must not date it."""
    rows = [
        _placeholder('go on', 1),
        _row('prompt-kprompt-0', 'prompt', {'text': 'go on'}, id=2),
        _pending_tool('tu-1', 3),
        _row('prompt-kprompt-1', 'prompt', {'text': 'go on'}, id=4),
    ]
    assert tool_pending_id('tu-1') not in _survivors(rows)


def test_a_late_previous_anchor_does_not_undate_the_next_turn():
    """Turn 1's echo lands after turn 2's placeholder."""
    rows = [
        _placeholder('first', 1),
        _pending_tool('tu-1', 2),
        _placeholder('second', 3),
        _row('prompt-kprompt-0', 'prompt', {'text': 'first'}, id=4),
        _pending_tool('tu-2', 5),
        _row('prompt-kprompt-1', 'prompt', {'text': 'second'}, id=6),
    ]
    survivors = _survivors(rows)
    assert tool_pending_id('tu-2') in survivors
    assert tool_pending_id('tu-1') not in survivors


def _insert(conn, row):
    conn.execute(
        "INSERT INTO session_spans (id, trace_id, span_id, name, start_time, "
        "attributes, status_code) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (row['id'], TID, row['span_id'], row['name'], row['start_time'],
         json.dumps(row['attributes']), row['status_code']))


def test_windowed_ceiling_dates_the_newest_turn_by_its_placeholder(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    try:
        for row in (_placeholder('fix defect', 101), _pending_tool('tu-1', 102),
                    _row('prompt-kprompt-0', 'prompt', {'text': 'fix defect'},
                         id=103)):
            _insert(conn, row)
        conn.commit()
        ceiling = _prompt_ceiling(conn, [TID])
    finally:
        conn.close()
    assert ceiling == 101
    rows = [_pending_tool('tu-1', 102),
            _row('prompt-kprompt-0', 'prompt', {'text': 'fix defect'}, id=103)]
    assert tool_pending_id('tu-1') in _survivors(rows, prompt_id_ceiling=ceiling)


def test_windowed_ceiling_without_a_placeholder_is_the_newest_prompt(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    try:
        for row in (_row('prompt-a', 'prompt', {'text': 'a'}, id=7),
                    _row('prompt-b', 'prompt', {'text': 'b'}, id=9)):
            _insert(conn, row)
        conn.commit()
        assert _prompt_ceiling(conn, [TID]) == 9
    finally:
        conn.close()
