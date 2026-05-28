"""Tests for the server-side-tool path through turn_trace.

`advisor` (and any future server-side tool) never fires PostToolUse, so
`post_tool_trace.py` never creates a `tool.<name>` row for it. The
turn_trace handler synthesises one from the transcript so the
session-trace view actually shows the call, and the tool_attribution
UPDATE has a row to land tokens on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hook_manager.handlers import turn_trace
from lib import hook_plugin
from lib.trace.transcript_usage import read_usage


@pytest.fixture
def _isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('REGIN_TURN_TRACE_STATE_DIR', str(tmp_path / 'state'))
    yield


@pytest.fixture
def _captured(monkeypatch):
    spans: list[dict] = []
    events: list[tuple[str, dict | list]] = []

    def fake_post_span(**kw):
        spans.append(kw)

    def fake_post_event(endpoint, data, agent_type=None):
        events.append((endpoint, data))

    monkeypatch.setattr(hook_plugin, 'post_span', fake_post_span)
    monkeypatch.setattr(hook_plugin, 'post_event', fake_post_event)
    return spans, events


_ADVISOR_TEXT = "Implementation looks correct. Verification checklist follows..."


def _advisor_transcript(tmp_path: Path, *, advisor_text: str = _ADVISOR_TEXT) -> str:
    """Write a two-turn transcript: advisor invocation + advisor_tool_result."""
    p = tmp_path / 'transcript.jsonl'
    entries = [
        {"type": "user", "uuid": "user-1", "parentUuid": None,
         "message": {"content": "ask the advisor"}},
        {
            "type": "assistant",
            "uuid": "asst-1",
            "parentUuid": "user-1",
            "timestamp": "2026-05-15T12:00:00Z",
            "advisorModel": "claude-opus-4-7",
            "message": {
                "id": "msg-1",
                "model": "claude-opus-4-7",
                "content": [
                    {"type": "text", "text": "consulting advisor"},
                    {"type": "server_tool_use",
                     "id": "srvtoolu_abcdefghij", "name": "advisor",
                     "input": {}},
                ],
                "usage": {
                    "input_tokens": 7, "output_tokens": 1329,
                    "cache_read_input_tokens": 233365,
                    "cache_creation_input_tokens": 4599,
                    "iterations": [
                        {"type": "advisor_message", "model": "claude-opus-4-7",
                         "input_tokens": 120043, "output_tokens": 6931,
                         "cache_read_input_tokens": 0,
                         "cache_creation_input_tokens": 0},
                    ],
                },
            },
        },
        {
            "type": "assistant",
            "uuid": "asst-2",
            "parentUuid": "asst-1",
            "timestamp": "2026-05-15T12:00:05Z",
            "message": {
                "id": "msg-2",
                "model": "claude-opus-4-7",
                "content": [
                    {"type": "advisor_tool_result",
                     "tool_use_id": "srvtoolu_abcdefghij",
                     "content": {"type": "advisor_result", "text": advisor_text}},
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1,
                          "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0},
            },
        },
    ]
    with open(p, 'w') as f:
        for e in entries:
            f.write(json.dumps(e) + '\n')
    return str(p)


def test_advisor_turn_emits_synthetic_tool_span(tmp_path, _isolated_state_dir, _captured):
    """An assistant turn containing a server_tool_use:advisor block must
    cause turn_trace to (a) post a synthetic `tool.advisor` span with a
    stable srvtool-* id, advisor_model attribute, and tool_use_id keyed
    to the srvtoolu_* id, plus (b) post a tool_attribution event with
    the advisor iteration's tokens. Without (a) the attribution UPDATE
    has no row to land on; without (b) the rollup stays empty."""
    spans, events = _captured
    usage = read_usage(_advisor_transcript(tmp_path))
    assert usage is not None
    turn_trace._post_live_turn_data(
        trace_id='trace-1',
        turns=usage.turns,
        fallback_model='claude-opus-4-7',
        capture_text=True,
        max_text_bytes=50_000,
        seen=set(),
    )

    tool_spans = [s for s in spans if s.get('name') == 'tool.advisor']
    assert len(tool_spans) == 1
    span = tool_spans[0]
    assert span['span_id'] == 'srvtool-srvtoolu_abcd'
    attrs = span['attributes']
    assert attrs['tool_name'] == 'advisor'
    assert attrs['tool_use_id'] == 'srvtoolu_abcdefghij'
    assert attrs['server_side'] is True
    assert attrs['advisor_model'] == 'claude-opus-4-7'
    # Advisor's actual response text is captured from the trailing
    # advisor_tool_result block.
    assert attrs['response_text'] == _ADVISOR_TEXT
    assert attrs['response_chars'] == len(_ADVISOR_TEXT)
    assert 'response_truncated' not in attrs
    # Timestamps must be naive-local (the server's _widen_envelopes can't
    # compare offset-aware against the rest of the trace's naive times).
    assert not span['start_time'].endswith('Z')
    assert '+' not in span['start_time'][10:]

    # tool_attribution must include the advisor call with iteration tokens.
    attrib_events = [d for name, d in events if name == 'tool_attribution']
    assert len(attrib_events) == 1
    calls = attrib_events[0]['tool_calls']
    advisor = next(c for c in calls if c['tool_use_id'] == 'srvtoolu_abcdefghij')
    assert advisor['name'] == 'advisor'
    assert advisor['output_tokens'] == 6931
    assert advisor['input_tokens'] == 120043


def test_replay_is_idempotent_via_seen_cache(tmp_path, _isolated_state_dir, _captured):
    """Once the per-session seen cache lists a turn's uuid, a re-run
    posts neither the synth span nor the attribution event — the cache
    is the client-side throttle the comment in turn_trace.py promises."""
    spans, events = _captured
    usage = read_usage(_advisor_transcript(tmp_path))
    assert usage is not None
    turn_trace._post_live_turn_data(
        trace_id='trace-1', turns=usage.turns,
        fallback_model='claude-opus-4-7', capture_text=True,
        max_text_bytes=50_000,
        seen={'asst-1'},  # turn already marked seen
    )
    assert not any(s.get('name') == 'tool.advisor' for s in spans)
    assert not any(name == 'tool_attribution' for name, _ in events)


def test_advisor_span_sorts_after_assistant_response_within_turn(
    tmp_path, _isolated_state_dir, _captured,
):
    """Both the `assistant_response` and the synthesised `tool.advisor`
    spans derive from the same transcript timestamp. If we posted them
    with identical timestamps the trace UI's chronological sort would
    render them in arbitrary order — observed in production as
    `tool.advisor` appearing *before* the response text that asked for
    it. Stagger the server-tool spans by +(i+1)ms so they always sort
    after the assistant_response in invocation order."""
    spans, _ = _captured
    usage = read_usage(_advisor_transcript(tmp_path))
    assert usage is not None
    turn_trace._post_live_turn_data(
        trace_id='trace-order-1', turns=usage.turns,
        fallback_model='claude-opus-4-7', capture_text=True,
        max_text_bytes=50_000,
        seen=set(),
    )
    resp = next(s for s in spans if s.get('name') == 'assistant_response')
    advisor = next(s for s in spans if s.get('name') == 'tool.advisor')
    assert resp['start_time'] < advisor['start_time'], (
        f"assistant_response ({resp['start_time']}) must sort before "
        f"tool.advisor ({advisor['start_time']}) — same turn"
    )


def test_thinking_only_turn_emits_distinct_span_name(
    tmp_path, _isolated_state_dir, _captured,
):
    """A turn that emitted only thinking blocks (no user-visible text)
    must NOT become an `assistant_response` span — that produces empty
    "response" rows in the conversation view. It lands as
    `assistant.thinking` so the conversation view ignores it but the
    trace timeline can still surface the reasoning event."""
    spans, _ = _captured
    transcript = tmp_path / 'thinking.jsonl'
    entries = [
        {"type": "user", "uuid": "u1", "parentUuid": None,
         "message": {"content": "go"}},
        {
            "type": "assistant",
            "uuid": "asst-think-only",
            "parentUuid": "u1",
            "timestamp": "2026-05-15T12:00:00Z",
            "message": {
                "id": "msg-tt",
                "model": "claude-opus-4-7",
                "content": [
                    {"type": "thinking", "thinking": "",
                     "signature": "q" * 512},
                    {"type": "tool_use", "id": "tu_x", "name": "Bash",
                     "input": {}},
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1,
                          "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0},
            },
        },
    ]
    with open(transcript, 'w') as f:
        for e in entries:
            f.write(json.dumps(e) + '\n')
    usage = read_usage(str(transcript))
    assert usage is not None
    turn_trace._post_live_turn_data(
        trace_id='trace-think-1', turns=usage.turns,
        fallback_model='claude-opus-4-7', capture_text=True,
        max_text_bytes=50_000,
        seen=set(),
    )
    response_spans = [s for s in spans if s.get('name') == 'assistant_response']
    thinking_spans = [s for s in spans if s.get('name') == 'assistant.thinking']
    assert response_spans == []
    assert len(thinking_spans) == 1
    s = thinking_spans[0]
    assert s['span_id'].startswith('think-')
    attrs = s['attributes']
    assert attrs['thinking_blocks'] == 1
    assert attrs['thinking_signature_bytes'] == 512
    assert 'text' not in attrs


def test_advisor_response_text_is_truncated_when_over_cap(tmp_path, _isolated_state_dir, _captured):
    """A multi-kilobyte advisor reply must be byte-capped at
    `max_text_bytes` and marked with `response_truncated: True` so the
    span attributes blob stays bounded."""
    spans, _ = _captured
    big_text = 'x' * 60_000
    usage = read_usage(_advisor_transcript(tmp_path, advisor_text=big_text))
    assert usage is not None
    turn_trace._post_live_turn_data(
        trace_id='trace-1', turns=usage.turns,
        fallback_model='claude-opus-4-7', capture_text=True,
        max_text_bytes=10_000,
        seen=set(),
    )
    tool_spans = [s for s in spans if s.get('name') == 'tool.advisor']
    assert len(tool_spans) == 1
    attrs = tool_spans[0]['attributes']
    assert attrs['response_truncated'] is True
    assert len(attrs['response_text'].encode('utf-8')) <= 10_000 + len('\n\n…[truncated]'.encode('utf-8'))
    assert attrs['response_text'].endswith('…[truncated]')
