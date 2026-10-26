"""`regin trace spawn` — the regin-lineage mod's report of one `agent.spawn`."""

from __future__ import annotations

import json
import sqlite3

import pytest
from typer.testing import CliRunner

from cli.commands.trace import trace_app
from lib import hook_plugin
from lib.orm.engine import get_connection
from lib.trace.agent_spawn import spawn_parents
from lib.trace.merge import merge_spans

_SID = 'sess-spawn'
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


def _spawn(record: dict):
    return runner.invoke(trace_app, ['spawn', '--session', _SID], input=json.dumps(record))


def _store(db_path, span: dict) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO session_spans (trace_id, span_id, name, start_time, attributes) "
            "VALUES (?, ?, ?, ?, ?)",
            (span['trace_id'], span['span_id'], span['name'], span['start_time'],
             json.dumps(span['attributes'])),
        )
        conn.commit()
    finally:
        conn.close()


def _parents():
    conn = get_connection()
    try:
        return spawn_parents(conn, _SID)
    finally:
        conn.close()


def test_spawn_event_records_agent_lineage(tmp_db, posted):
    result = _spawn({'agentId': 'child-1', 'parentAgentId': 'parent-1',
                     'tool_use_id': 'toolu_1', 'subagentType': 'Explore'})
    assert result.exit_code == 0, result.output
    assert len(posted) == 1
    span = posted[0]
    assert span['trace_id'] == _SID
    assert span['name'] == 'agent.spawn'
    assert span['span_id'] == 'agentspawn-child-1'
    assert span['attributes']['spawn_agent_id'] == 'child-1'
    assert span['attributes']['spawn_parent_agent_id'] == 'parent-1'
    assert span['attributes']['spawn_tool_use_id'] == 'toolu_1'
    assert span['attributes']['spawn_subagent_type'] == 'Explore'
    assert 'agent_id' not in span['attributes']

    _store(tmp_db, span)
    assert _parents() == {'child-1': 'parent-1'}


def test_respawn_report_lands_on_the_same_record(tmp_db, posted):
    record = {'agentId': 'child-1', 'parentAgentId': None, 'tool_use_id': 'toolu_1',
              'subagentType': 'Explore'}
    _spawn(record)
    _spawn(record)
    assert posted[0]['span_id'] == posted[1]['span_id']
    for span in posted:
        _store(tmp_db, span)
    assert _parents() == {'child-1': None}


def test_spawn_without_agent_id_is_rejected(posted):
    result = _spawn({'parentAgentId': 'p', 'tool_use_id': 't', 'subagentType': 'x'})
    assert result.exit_code == 2
    assert posted == []


def test_unreachable_ingest_exits_nonzero(monkeypatch):
    monkeypatch.setattr(hook_plugin, 'post_event', lambda *a, **k: False)
    result = _spawn({'agentId': 'a', 'subagentType': 'x'})
    assert result.exit_code == 1


def test_spawn_record_is_not_a_tree_node(posted):
    _spawn({'agentId': 'child-1', 'subagentType': 'Explore'})
    row = {**posted[0], 'id': 5, 'source': 'hook'}
    other = {'id': 4, 'trace_id': _SID, 'span_id': 'x', 'name': 'tool.Read',
             'source': 'hook', 'status_code': 'OK', 'start_time': row['start_time'],
             'end_time': row['start_time'], 'attributes': {'tool_use_id': 't'}}
    names = [s['name'] for s in merge_spans([other, row])]
    assert names == ['tool.Read']
