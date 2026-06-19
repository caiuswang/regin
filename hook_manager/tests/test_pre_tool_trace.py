"""Tests for the PreToolUse pending-span handler."""

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import pre_tool_trace


def _p(event, **kw):
    return HookPayload.from_stdin_json(event, {'hook_event_name': event, **kw})


@pytest.fixture
def captured_spans(monkeypatch):
    import lib.hook_plugin as hp
    spans: list[dict] = []
    monkeypatch.setattr(hp, 'post_span', lambda **kw: spans.append(kw))
    return spans


def test_emits_pending_span_for_askuserquestion(captured_spans):
    from lib.trace.pending_spans import tool_pending_id
    pre_tool_trace.handle(_p(
        'PreToolUse', session_id='s1', tool_name='AskUserQuestion',
        tool_use_id='toolu_abc123456789',
        tool_input={'questions': [{
            'question': 'Pick one?', 'header': 'Scope',
            'options': [{'label': 'A', 'description': 'a'},
                        {'label': 'B', 'description': 'b'}],
            'multiSelect': False,
        }]},
    ))
    assert len(captured_spans) == 1
    s = captured_spans[0]
    assert s['name'] == 'tool.AskUserQuestion'
    assert s['span_id'] == tool_pending_id('toolu_abc123456789')
    assert s['span_id'].startswith('pending-')
    assert s['status_code'] == 'PENDING'
    assert s['attributes']['tool_use_id'] == 'toolu_abc123456789'
    assert s['attributes']['live'] is True
    # the question renders while pending
    q = s['attributes']['questions']
    assert q[0]['question'] == 'Pick one?'
    assert [o['label'] for o in q[0]['options']] == ['A', 'B']


def test_emits_pending_span_for_exitplanmode(captured_spans):
    pre_tool_trace.handle(_p(
        'PreToolUse', session_id='s1', tool_name='ExitPlanMode',
        tool_use_id='toolu_plan999', tool_input={'plan': '...'},
    ))
    assert len(captured_spans) == 1
    assert captured_spans[0]['name'] == 'tool.ExitPlanMode'
    assert captured_spans[0]['status_code'] == 'PENDING'
    assert 'questions' not in captured_spans[0]['attributes']


def test_emits_pending_span_for_bash_with_input_preview(captured_spans):
    from lib.trace.pending_spans import tool_pending_id
    pre_tool_trace.handle(_p(
        'PreToolUse', session_id='s1', tool_name='Bash',
        tool_use_id='toolu_bash1', tool_input={'command': 'sleep 30'},
    ))
    assert len(captured_spans) == 1
    s = captured_spans[0]
    assert s['name'] == 'tool.Bash'
    assert s['span_id'] == tool_pending_id('toolu_bash1')
    assert s['status_code'] == 'PENDING'
    # The command shows on the pending card via the same flat `command_preview`
    # key the resolved Bash card reads — NOT a raw tool_input dump, which the
    # conversation labellers (fullLabel / BashCard) ignore.
    a = s['attributes']
    assert a['command_preview'] == 'sleep 30'
    assert 'tool_input' not in a


def test_pending_bash_carries_full_command_when_long(captured_spans):
    # A command longer than the preview cap also gets the full `command` key,
    # so the pending card routes through BashCard with an expandable command
    # block — mirroring the resolved card.
    long_cmd = 'echo ' + 'x' * 300
    pre_tool_trace.handle(_p(
        'PreToolUse', session_id='s1', tool_name='Bash',
        tool_use_id='toolu_bash2', tool_input={'command': long_cmd},
    ))
    a = captured_spans[0]['attributes']
    assert a['command_preview'].endswith('…')
    assert a['command'] == long_cmd
    assert 'tool_input' not in a


def test_pending_websearch_carries_flat_query(captured_spans):
    # WebSearch pending card reads `query` (same flat key the resolved card
    # reads), not a raw tool_input dump.
    pre_tool_trace.handle(_p(
        'PreToolUse', session_id='s1', tool_name='WebSearch',
        tool_use_id='toolu_ws1', tool_input={'query': 'opus 4.8 pricing'},
    ))
    a = captured_spans[0]['attributes']
    assert a['query'] == 'opus 4.8 pricing'
    assert 'tool_input' not in a


def test_pending_webfetch_carries_flat_url(captured_spans):
    pre_tool_trace.handle(_p(
        'PreToolUse', session_id='s1', tool_name='WebFetch',
        tool_use_id='toolu_wf1',
        tool_input={'url': 'https://example.com', 'prompt': 'summarize'},
    ))
    a = captured_spans[0]['attributes']
    assert a['url'] == 'https://example.com'
    assert a['fetch_prompt'] == 'summarize'
    assert 'tool_input' not in a


def test_pending_mcp_keeps_raw_input_for_detail(captured_spans):
    # MCP tools have no flat-key builder; the label already shows
    # `server · endpoint` from the span name, so the raw tool_input is kept
    # only to feed the detail panel.
    pre_tool_trace.handle(_p(
        'PreToolUse', session_id='s1', tool_name='mcp__server__do',
        tool_use_id='toolu_mcp9', tool_input={'arg': 1},
    ))
    a = captured_spans[0]['attributes']
    assert a['tool_input'] == {'arg': 1}


def test_emits_pending_span_for_agent_with_launch_metadata(captured_spans):
    # The pending tool.Agent twin must carry the same structured launch attrs
    # as the resolved span (subagent_type / description / prompt) so the live
    # subagent row can show its goal before the run finishes.
    from lib.trace.pending_spans import tool_pending_id
    pre_tool_trace.handle(_p(
        'PreToolUse', session_id='s1', tool_name='Agent',
        tool_use_id='toolu_agent1',
        tool_input={'subagent_type': 'Explore',
                    'description': 'Find subagent display path',
                    'prompt': 'Investigate how subagent spans render.'},
    ))
    assert len(captured_spans) == 1
    s = captured_spans[0]
    assert s['name'] == 'tool.Agent'
    assert s['span_id'] == tool_pending_id('toolu_agent1')
    assert s['status_code'] == 'PENDING'
    a = s['attributes']
    assert a['subagent_type'] == 'Explore'
    assert a['description'] == 'Find subagent display path'
    assert a['prompt'] == 'Investigate how subagent spans render.'
    # structured attrs, not the raw tool_input dump
    assert 'tool_input' not in a


def test_emits_pending_span_for_mcp_tool(captured_spans):
    pre_tool_trace.handle(_p(
        'PreToolUse', session_id='s1', tool_name='mcp__server__do',
        tool_use_id='toolu_mcp1', tool_input={'x': 1},
    ))
    assert len(captured_spans) == 1
    assert captured_spans[0]['name'] == 'tool.mcp__server__do'


def test_no_pending_span_for_instant_tool(captured_spans):
    # Read/Edit/Grep resolve within a poll cycle — no pending card (no flicker).
    pre_tool_trace.handle(_p(
        'PreToolUse', session_id='s1', tool_name='Read',
        tool_use_id='toolu_read1', tool_input={'file_path': '/tmp/x'},
    ))
    assert captured_spans == []


def test_no_pending_span_without_tool_use_id(captured_spans):
    # Without a tool_use_id there's no key for ingest to retire on — skip.
    pre_tool_trace.handle(_p(
        'PreToolUse', session_id='s1', tool_name='AskUserQuestion',
        tool_input={'questions': []},
    ))
    assert captured_spans == []
