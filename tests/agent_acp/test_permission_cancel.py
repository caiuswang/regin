"""D309 — a permission the ACP client cancels must not stay a blocker.

CodeBuddy launched over ACP both runs its hooks (which file a PENDING
`permission.request`) and asks the wire (which the client answers
`cancelled`). Nothing else resolves the hook's park, so it showed as a
blocker card with no answer to give.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from lib.agent_acp import client as acp_client
from lib.agent_messages import blockers
from lib.trace.pending_spans import perm_pending_id

_TRACE = '01a162f4-acp-run'
_CALL = 'chatcmpl-tool-8698e66bca7b7663'


@pytest.fixture(autouse=True)
def _unreachable(monkeypatch):
    monkeypatch.setattr(
        'web.blueprints.trace.sessions._bridge_reachability',
        lambda trace_id: {'bridge_reachable': False, 'bridge_pane': None,
                          'sdk_owned': False})


def _insert(tmp_db, **span):
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO session_spans "
            "(trace_id, span_id, name, kind, start_time, status_code, attributes) "
            "VALUES (?, ?, ?, 'internal', ?, ?, ?)",
            (span['trace_id'], span.get('span_id') or 'resolved-1', span['name'],
             datetime.now().isoformat(), span.get('status_code', 'OK'),
             json.dumps(span.get('attributes') or {})))
        conn.commit()
    finally:
        conn.close()


def _park(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    try:
        now = datetime.now().isoformat()
        conn.execute("INSERT INTO sessions (trace_id, started_at, last_seen, "
                     "status, agent_type) VALUES (?, ?, ?, 'active', 'codebuddy')",
                     (_TRACE, now, now))
        conn.commit()
    finally:
        conn.close()
    _insert(tmp_db, trace_id=_TRACE, span_id=perm_pending_id(_CALL),
            name='permission.request', status_code='PENDING',
            attributes={'tool_name': 'Bash', 'tool_use_id': _CALL,
                        'agent_type': 'codebuddy'})


def _request(call_id=_CALL):
    """The shape `codebuddy --acp` sends (dist/codebuddy.js requestPermission)."""
    return {'sessionId': _TRACE,
            'options': [{'kind': 'allow', 'name': 'Yes', 'optionId': 'allow_once'}],
            'toolCall': {'toolCallId': call_id,
                         'rawInput': {'command': 'git worktree remove x'},
                         '_meta': {'codebuddy.ai/toolName': 'Bash'}}}


def test_a_cancelled_acp_permission_retires_the_hook_park(tmp_db, monkeypatch):
    posted = []
    monkeypatch.setattr('lib.hook_plugin.post_span',
                        lambda **span: posted.append(span) or True)
    _park(tmp_db)
    assert len(blockers.live_blockers()) == 1

    result = acp_client._cancel_permission(_request())

    assert result['outcome'] == {'outcome': 'cancelled'}
    [span] = posted
    assert span['name'] == 'permission.denied'
    assert span['attributes']['tool_use_id'] == _CALL
    assert span['attributes']['tool_name'] == 'Bash'
    _insert(tmp_db, **span)
    assert blockers.live_blockers() == []


def test_a_kimi_scoped_call_id_maps_to_the_hook_id(monkeypatch):
    posted = []
    monkeypatch.setattr('lib.hook_plugin.post_span',
                        lambda **span: posted.append(span) or True)
    acp_client._cancel_permission(_request(call_id=f'0:{_CALL}'))
    assert posted[0]['attributes']['tool_use_id'] == _CALL


def test_a_failed_record_still_answers_the_agent(monkeypatch):
    def boom(**_span):
        raise OSError('ingest down')
    monkeypatch.setattr('lib.hook_plugin.post_span', boom)
    result = acp_client._cancel_permission(_request())
    assert result['outcome'] == {'outcome': 'cancelled'}
