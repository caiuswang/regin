"""`regin trace decision` — the regin-permit mod's report of one `tool.check`."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from cli.commands.trace import trace_app
from lib import hook_plugin
from lib.trace.merge import merge_spans

_SID = 'sess-decision'
_TS = '2026-10-26T10:00:00.000000Z'
runner = CliRunner()


@pytest.fixture
def posted(monkeypatch):
    spans: list[dict] = []

    def _capture(endpoint, data, agent_type=None):
        if endpoint == 'session_spans':
            spans.extend(data if isinstance(data, list) else [data])
        return True

    monkeypatch.setattr(hook_plugin, 'post_event', _capture)
    return spans


def _decide(record: dict):
    return runner.invoke(trace_app, ['decision', '--session', _SID], input=json.dumps(record))


def _tool(span_id: str, tool_use_id: str, row_id: int, name: str = 'tool.Bash') -> dict:
    return {'id': row_id, 'trace_id': _SID, 'span_id': span_id, 'name': name,
            'source': 'hook', 'status_code': 'OK', 'start_time': _TS, 'end_time': _TS,
            'attributes': {'tool_use_id': tool_use_id}}


def _merged(posted: list[dict], *others: dict) -> dict:
    rows = [*others, *({**p, 'id': 100 + i, 'source': 'hook'} for i, p in enumerate(posted))]
    return {s['span_id']: s for s in merge_spans(rows)}


def test_tool_check_records_decider(posted):
    result = _decide({'tool_use_id': 'toolu_1', 'decision': 'allow',
                      'rule': 'Bash(git status)', 'decider': 'rule'})
    assert result.exit_code == 0, result.output
    assert len(posted) == 1
    row = posted[0]
    assert row['trace_id'] == _SID
    assert row['name'] == 'permission.decision'
    assert row['span_id'] == 'tooldecision-toolu_1'
    assert 'tool_use_id' not in row['attributes']

    merged = _merged(posted, _tool('s-call', 'toolu_1', 1), _tool('s-other', 'toolu_2', 2))
    assert merged['s-call']['attributes']['permission'] == {
        'decision': 'allow', 'rule': 'Bash(git status)', 'decider': 'rule'}
    assert 'permission' not in merged['s-other']['attributes']
    assert 'tooldecision-toolu_1' not in merged


def test_tool_check_decision_carries_agent_id(posted):
    result = _decide({'tool_use_id': 'toolu_1', 'decision': 'ask', 'reason': 'Run rm?',
                      'hook': 'PreToolUse', 'agentId': 'a1'})
    assert result.exit_code == 0, result.output

    permission = _merged(posted, _tool('s-call', 'toolu_1', 1))['s-call']['attributes']['permission']
    assert permission == {'decision': 'ask', 'reason': 'Run rm?', 'hook': 'PreToolUse',
                          'agent_id': 'a1'}
    assert 'agent_id' not in posted[0]['attributes']


def test_main_loop_decision_has_no_agent_id(posted):
    _decide({'tool_use_id': 'toolu_1', 'decision': 'allow'})
    assert posted[0]['attributes']['permission'] == {'decision': 'allow'}


def test_redelivered_decision_lands_on_the_same_row(posted):
    record = {'tool_use_id': 'toolu_1', 'decision': 'deny', 'reason': 'no'}
    _decide(record)
    _decide(record)
    assert posted[0]['span_id'] == posted[1]['span_id']


def test_decision_reaches_the_pending_placeholder_too(posted):
    _decide({'tool_use_id': 'toolu_1', 'decision': 'ask'})
    pending = {**_tool('pending-toolu_1', 'toolu_1', 1), 'status_code': 'PENDING'}
    merged = _merged(posted, pending)
    assert merged['pending-toolu_1']['attributes']['permission'] == {'decision': 'ask'}


def test_only_tool_spans_take_the_verdict(posted):
    _decide({'tool_use_id': 'toolu_1', 'decision': 'allow'})
    other = _tool('s-marker', 'toolu_1', 1, name='subagent.start')
    assert 'permission' not in _merged(posted, other)['s-marker']['attributes']


@pytest.mark.parametrize('record', [
    {'decision': 'allow'},
    {'tool_use_id': '', 'decision': 'allow'},
    {'tool_use_id': 'toolu_1', 'decision': 'maybe'},
])
def test_malformed_decision_is_rejected(posted, record):
    assert _decide(record).exit_code == 2
    assert posted == []


def test_unreachable_ingest_exits_nonzero(monkeypatch):
    monkeypatch.setattr(hook_plugin, 'post_event', lambda *a, **k: False)
    assert _decide({'tool_use_id': 't', 'decision': 'allow'}).exit_code == 1
