"""A Kimi `Stop` closes a turn for `turn_state` only when the main wire says so.

Kimi fires a parent-session `Stop` ahead of every `SubagentStop`, mid-turn, so
the event alone cannot park the session. The main agent's own wire records
`agent.turn.started` / `agent.turn.ended` (protocol 1.5); those decide.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from hook_manager.handlers.turn_trace import entry
from lib.trace.kimi_transcript import read_usage_kimi


def _wire(tmp_path, *records):
    path = tmp_path / 'wire.jsonl'
    path.write_text(''.join(json.dumps(r) + '\n' for r in records))
    return str(path)


_PROMPT = {'type': 'turn.prompt', 'input': [{'type': 'text', 'text': 'hi'}],
           'origin': {'kind': 'user'}, 'promptId': 'msg_1', 'turnId': 0, 'time': 2}
_STARTED = {'type': 'agent.turn.started', 'turnId': 0, 'queueItemId': 'msg_1', 'time': 1}
_ENDED = {'type': 'agent.turn.ended', 'turnId': 0, 'outcome': 'done', 'time': 3}
_CANCEL = {'type': 'turn.cancel', 'turnId': 0, 'reason': 'user_cancelled', 'time': 3}


@pytest.mark.parametrize('records, expected', [
    ((_STARTED, _PROMPT), True),
    ((_STARTED, _PROMPT, _ENDED), False),
    ((_STARTED, _PROMPT, _CANCEL), False),   # interrupted, no turn.ended ever logged
    ((_PROMPT,), None),
])
def test_wire_reports_whether_the_main_turn_is_open(tmp_path, records, expected):
    usage = read_usage_kimi(_wire(tmp_path, *records))
    assert usage.main_turn_open is expected


@pytest.fixture
def posted(monkeypatch):
    import lib.hook_plugin as hp
    spans: list[dict] = []
    monkeypatch.setattr(hp, 'post_span', lambda **kw: spans.append(kw))
    monkeypatch.setattr(entry, '_turn_span_wanted', lambda *_a: True)
    return spans


def _emit(monkeypatch, event, turn_open):
    monkeypatch.setattr(entry, '_ingest_transcript_usage',
                        lambda *_a, **_k: ('kimi-model', 'step-1', turn_open))
    provider = SimpleNamespace(transcript_format='kimi',
                               resolve_transcript_path=lambda _p: '/wire.jsonl')
    entry._emit_span(SimpleNamespace(event=event, session_id='session_k',
                                     raw={}, resolved_provider=provider))


def _turn_attrs(posted):
    return [s['attributes'] for s in posted if s['name'] == 'turn']


def test_stop_after_the_wire_closed_the_turn_is_a_boundary(monkeypatch, posted):
    _emit(monkeypatch, 'Stop', turn_open=False)
    assert _turn_attrs(posted)[0]['hook_event'] == 'Stop'


@pytest.mark.parametrize('event, turn_open', [
    ('Stop', True),          # the parent Stop Kimi fires before a SubagentStop
    ('Stop', None),          # a 1.4 wire: no lifecycle records at all
    ('UserPromptSubmit', False),
])
def test_other_kimi_turn_spans_stay_non_boundary(monkeypatch, posted, event, turn_open):
    _emit(monkeypatch, event, turn_open)
    assert 'hook_event' not in _turn_attrs(posted)[0]
