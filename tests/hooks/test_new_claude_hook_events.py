"""Claude Code hook events added in 2.1.219–2.1.251: PreModelSwitch,
PostModelSwitch, PostToolBatch, DirectoryAdded, MessageDisplay.

Every payload goes through the real runner and the full registry, so a handler
wired to the wrong event, or a catch-all that starts writing spans, shows up
here rather than only in a direct handler call.
"""

from __future__ import annotations

import io
import json

import pytest

from hook_manager.core import SPEC_EVENTS, STDOUT_IS_DATA
from hook_manager.registry import REGISTRY
from hook_manager.runner import run
from lib import hook_plugin, hooks_wiring
from lib.providers.claude import ClaudeProvider
from lib.trace.merge import merge_spans
from lib.trace.payload_validation import validate_event

NEW_EVENTS = ('PreModelSwitch', 'PostModelSwitch', 'PostToolBatch',
              'DirectoryAdded', 'MessageDisplay')


@pytest.fixture
def posted(monkeypatch):
    spans: list[dict] = []

    def _capture(endpoint, data, agent_type=None):
        if endpoint == 'session_spans':
            spans.extend(data if isinstance(data, list) else [data])
        return True

    monkeypatch.setattr(hook_plugin, 'post_event', _capture)
    return spans


def _dispatch(event: str, **fields) -> tuple[int, str]:
    body = {'hook_event_name': event, 'session_id': 'sess-a4',
            'cwd': '/tmp/proj', 'prompt_id': 'p-1', **fields}
    out = io.StringIO()
    rc = run(event, REGISTRY, json.dumps(body), out, agent_type='claude')
    return rc, out.getvalue()


def _row(row_id: int, span: dict) -> dict:
    return {**span, 'id': row_id, 'source': 'hook'}


def _tool_span(row_id: int, tool_use_id: str) -> dict:
    return {'id': row_id, 'trace_id': 'sess-a4', 'span_id': f'tool-{tool_use_id}',
            'name': 'tool.Read', 'source': 'hook', 'status_code': 'OK',
            'start_time': f'2026-10-26T10:00:0{row_id}', 'end_time': f'2026-10-26T10:00:0{row_id}',
            'attributes': {'tool_use_id': tool_use_id}}


PAYLOADS = {
    'PreModelSwitch': {'from_model': 'claude-sonnet-5', 'to_model': 'claude-opus-5',
                       'requested_model': 'opus', 'source': 'command'},
    'PostModelSwitch': {'from_model': 'claude-opus-5-5', 'to_model': 'claude-sonnet-5',
                        'requested_model': None, 'source': 'auto'},
    'PostToolBatch': {'tool_calls': [{'tool_name': 'Read', 'tool_input': {},
                                      'tool_use_id': 'toolu_a', 'tool_response': 'x'}]},
    'DirectoryAdded': {'directory': '/tmp/other', 'source': 'slash_command'},
    'MessageDisplay': {'turn_id': 't1', 'message_id': 'm1', 'index': 0,
                       'final': True, 'delta': 'hello\n'},
}


def test_new_claude_hook_events_are_routed(tmp_path):
    assert set(NEW_EVENTS) <= SPEC_EVENTS
    assert not set(NEW_EVENTS) & STDOUT_IS_DATA

    path = tmp_path / 'settings.json'
    provider = ClaudeProvider({'hook_settings_path': path})
    hooks_wiring.install_hook_manager(provider, str(path))
    installed = json.loads(path.read_text())['hooks']
    for event in NEW_EVENTS:
        commands = [h['command'] for entry in installed.get(event, [])
                    for h in entry.get('hooks', [])]
        assert commands == [hooks_wiring.hook_manager_command(event, provider)], event


@pytest.mark.parametrize('event', NEW_EVENTS)
def test_new_event_payload_has_a_schema(event):
    findings = validate_event(event, {'hook_event_name': event, **PAYLOADS[event]})
    assert [f.drift_kind for f in findings] == []


@pytest.mark.parametrize('event', NEW_EVENTS)
def test_new_event_response_does_not_block(event, posted):
    rc, out = _dispatch(event, **PAYLOADS[event])
    assert rc == 0
    response = json.loads(out)
    assert 'decision' not in response
    assert 'displayContent' not in json.dumps(response)


@pytest.mark.parametrize('event', ['DirectoryAdded', 'MessageDisplay', 'PreModelSwitch'])
def test_route_only_events_write_no_span(event, posted):
    _dispatch(event, **PAYLOADS[event])
    assert posted == []


def test_post_model_switch_records_model_switch_span(posted):
    _dispatch('PostModelSwitch', from_model='claude-opus-5-5',
              to_model='claude-sonnet-5', source='auto', requested_model=None,
              context_tokens=182340, estimated_cache_write_usd=0.61)
    assert [s['name'] for s in posted] == ['model.switch']
    attrs = posted[0]['attributes']
    assert attrs['from'] == 'claude-opus-5-5'
    assert attrs['to'] == 'claude-sonnet-5'
    assert attrs['reason'] == 'auto'
    assert attrs['context_tokens'] == 182340
    assert 'requested_model' not in attrs
    assert attrs['source_prompt_id'] == 'p-1'


def test_post_tool_batch_stamps_batch_on_tool_spans(posted):
    _dispatch('PostToolBatch', tool_calls=[
        {'tool_name': 'Read', 'tool_input': {}, 'tool_use_id': 'toolu_a', 'tool_response': '1'},
        {'tool_name': 'Read', 'tool_input': {}, 'tool_use_id': 'toolu_b', 'tool_response': '2'},
    ])
    assert len(posted) == 1
    batch_row = _row(9, posted[0])

    merged = merge_spans([_tool_span(1, 'toolu_a'), _tool_span(2, 'toolu_b'),
                          _tool_span(3, 'toolu_c'), batch_row])
    by_tu = {s['attributes'].get('tool_use_id'): s for s in merged}
    assert set(by_tu) == {'toolu_a', 'toolu_b', 'toolu_c'}
    batch_id = by_tu['toolu_a']['attributes']['tool_batch_id']
    assert batch_id and by_tu['toolu_b']['attributes']['tool_batch_id'] == batch_id
    assert 'tool_batch_id' not in by_tu['toolu_c']['attributes']
    assert by_tu['toolu_a']['attributes']['derived_from_id'] == 9


def test_two_batches_never_share_an_id(posted):
    for tu in ('toolu_a', 'toolu_b'):
        _dispatch('PostToolBatch', tool_calls=[{'tool_use_id': tu}])
    merged = merge_spans([_tool_span(1, 'toolu_a'), _tool_span(2, 'toolu_b'),
                          _row(8, posted[0]), _row(9, posted[1])])
    ids = {s['attributes']['tool_batch_id'] for s in merged}
    assert len(ids) == 2


def test_redelivered_batch_keeps_its_span_id(posted):
    calls = [{'tool_use_id': 'toolu_b'}, {'tool_use_id': 'toolu_a'}]
    _dispatch('PostToolBatch', tool_calls=calls)
    _dispatch('PostToolBatch', tool_calls=list(reversed(calls)))
    assert posted[0]['span_id'] == posted[1]['span_id']


def test_batch_without_tool_use_ids_writes_nothing(posted):
    rc, _ = _dispatch('PostToolBatch', tool_calls=[{'tool_name': 'Read'}])
    assert rc == 0
    assert posted == []


def test_window_without_batches_is_unchanged():
    spans = [_tool_span(1, 'toolu_a')]
    assert merge_spans(spans)[0]['attributes'] == {'tool_use_id': 'toolu_a'}
