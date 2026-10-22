"""A CodeBuddy-family `Stop` closes a turn for `turn_state` once the transcript
shows the turn ended — including the stub an interrupt leaves behind."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from hook_manager.handlers.turn_trace import entry
from lib.providers.codebuddy import WorkBuddyProvider
from lib.trace.codebuddy_transcript import read_usage_codebuddy

SID = '8a5fd433-b241-48c5-88ce-e32d07fae411'
_PD = {'conversationRequestId': 'req-1', 'model': 'hy3'}

_PROMPT = {'id': 'u1', 'type': 'message', 'role': 'user', 'timestamp': 1,
           'content': [{'type': 'input_text', 'text': 'fix it'}]}
_THINK = {'id': 'r1', 'type': 'reasoning', 'timestamp': 2,
          'providerData': {**_PD, 'messageId': 'm1'},
          'rawContent': [{'type': 'reasoning_text', 'text': 'hmm'}]}
_LEAD_TEXT = {'id': 'a1', 'type': 'message', 'role': 'assistant', 'status': 'completed',
              'timestamp': 3, 'providerData': {**_PD, 'messageId': 'm1'},
              'content': [{'type': 'output_text', 'text': 'Let me look.'}]}
_CALL = {'id': 'c1', 'type': 'function_call', 'callId': 'call-1', 'name': 'Bash',
         'arguments': '{"command": "ls"}', 'timestamp': 4,
         'providerData': {**_PD, 'messageId': 'm1'}}
_RESULT = {'id': 'c2', 'type': 'function_call_result', 'callId': 'call-1', 'name': 'Bash',
           'status': 'completed', 'output': {'type': 'text', 'text': 'ok'}, 'timestamp': 5,
           'providerData': _PD}
_ANSWER = {'id': 'a2', 'type': 'message', 'role': 'assistant', 'status': 'completed',
           'timestamp': 6, 'providerData': {**_PD, 'messageId': 'm2'},
           'content': [{'type': 'output_text', 'text': 'Done.'}]}
_INTERRUPTED = {'id': 'a3', 'type': 'message', 'role': 'assistant', 'status': 'incomplete',
                'timestamp': 6, 'content': [{'type': 'output_text', 'text': 'Interrupted by user'}],
                'providerData': {'skipRun': True, 'conversationRequestId': 'req-1',
                                 'error': {'message': 'Interrupted by user'}}}
_LOCAL_CMD = {'id': 'u2', 'type': 'message', 'role': 'user', 'timestamp': 7,
              'providerData': {'skipRun': True},
              'content': [{'type': 'input_text', 'text': '<system-reminder>caveat'}]}
_TITLE = {'id': 't1', 'type': 'ai-title', 'aiTitle': 'Fix it', 'timestamp': 7}


def _transcript(tmp_path, *records):
    path = tmp_path / f'{SID}.jsonl'
    path.write_text(''.join(json.dumps({**r, 'sessionId': SID}) + '\n' for r in records))
    return str(path)


@pytest.mark.parametrize('records, expected', [
    ((_PROMPT, _THINK, _LEAD_TEXT, _CALL, _RESULT, _THINK, _CALL, _INTERRUPTED), False),
    ((_PROMPT, _THINK, _LEAD_TEXT, _CALL, _RESULT, _ANSWER), False),
    ((_PROMPT, _ANSWER, _TITLE), False),
    ((_PROMPT, _ANSWER, _LOCAL_CMD), False),
    ((_PROMPT, _THINK, _LEAD_TEXT, _CALL), True),
    ((_PROMPT, _THINK, _LEAD_TEXT), False),
    ((_PROMPT, _LEAD_TEXT, _CALL, _RESULT), True),
    ((_PROMPT, _THINK), True),
    ((_PROMPT,), True),
    ((_PROMPT, _ANSWER, _PROMPT), True),
])
def test_transcript_reports_whether_the_main_turn_is_open(tmp_path, records, expected):
    usage = read_usage_codebuddy(_transcript(tmp_path, *records))
    assert usage.main_turn_open is expected


def _emit(monkeypatch, tmp_path, event, *records):
    import lib.hook_plugin as hp
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / 'seen'))
    spans: list[dict] = []
    monkeypatch.setattr(hp, 'post_span', lambda **kw: spans.append(kw))
    monkeypatch.setattr(entry, '_post_transcript_usage', lambda *_a, **_k: None)
    path = _transcript(tmp_path, *records)
    provider = WorkBuddyProvider()
    monkeypatch.setattr(provider, 'resolve_transcript_path', lambda _p: path)
    entry._emit_span(SimpleNamespace(event=event, session_id=SID, raw={},
                                     resolved_provider=provider))
    return [s['attributes'] for s in spans if s['name'] == 'turn']


def test_stop_after_an_interrupt_is_a_boundary(monkeypatch, tmp_path):
    attrs = _emit(monkeypatch, tmp_path, 'Stop', _PROMPT, _THINK, _CALL, _INTERRUPTED)
    assert attrs[0]['hook_event'] == 'Stop'


def test_stop_with_a_tool_call_in_flight_is_not_a_boundary(monkeypatch, tmp_path):
    attrs = _emit(monkeypatch, tmp_path, 'Stop', _PROMPT, _THINK, _CALL)
    assert 'hook_event' not in attrs[0]
