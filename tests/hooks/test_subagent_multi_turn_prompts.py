"""A subagent's transcript can hold MORE than one user turn.

A `SendMessage` continuation appends another user turn to the same sidechain
transcript and the agent answers it in a second working period with its own
start/stop markers. The emitter used to read only the first turn and post a
single `prompt-sa-<agent_id>` span, so the resumed run's card had no prompt to
show — the launch prompt describes the first run alone.
"""

from __future__ import annotations

import json

from hook_manager.handlers.subagent_lifecycle import (
    _prompt_span_id, _user_prompts,
)

AGENT = 'a7f9ac69cf5ca6054'


def _write(tmp_path, entries) -> str:
    path = tmp_path / 'agent-x.jsonl'
    path.write_text('\n'.join(json.dumps(e) for e in entries) + '\n')
    return str(path)


def _user(text, ts, uuid):
    return {'type': 'user', 'uuid': uuid, 'timestamp': ts,
            'message': {'content': [{'type': 'text', 'text': text}]}}


def _tool_result(uuid):
    return {'type': 'user', 'uuid': uuid, 'timestamp': '2026-04-18T12:00:05Z',
            'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'toolu_x',
                 'content': 'ok'}]}}


def test_every_user_turn_of_a_resumed_agent_is_read(tmp_path):
    path = _write(tmp_path, [
        _user('verify the build', '2026-04-18T12:00:00Z', 'u-first'),
        {'type': 'assistant', 'uuid': 'a1'},
        _user('The coordinator sent a message: re-verify',
              '2026-04-18T12:05:00Z', 'u-second'),
    ])
    assert [p[0] for p in _user_prompts(path)] == [
        'verify the build', 'The coordinator sent a message: re-verify']


def test_tool_results_are_not_prompts(tmp_path):
    """Most of a transcript's `type: user` entries are tool results; only a
    top-level text block is something the agent was asked."""
    path = _write(tmp_path, [
        _user('verify the build', '2026-04-18T12:00:00Z', 'u-first'),
        _tool_result('u-tool'),
    ])
    assert len(_user_prompts(path)) == 1


def test_a_transcript_with_no_user_turn_yields_nothing(tmp_path):
    assert _user_prompts(_write(tmp_path, [{'type': 'assistant', 'uuid': 'a'}])) == []


def test_an_unreadable_transcript_yields_nothing(tmp_path):
    assert _user_prompts(str(tmp_path / 'missing.jsonl')) == []


def test_the_launch_prompt_keeps_its_bare_id():
    """It anchors the child trace's root and readers match it by name."""
    assert _prompt_span_id(AGENT, 0, 'u-first') == f'prompt-sa-{AGENT}'


def test_a_later_turn_is_keyed_on_its_transcript_entry():
    """Deterministic, so re-scanning the transcript re-posts the same row
    instead of accumulating one per pass."""
    first = _prompt_span_id(AGENT, 1, 'u-second-0123456789')
    assert first == _prompt_span_id(AGENT, 1, 'u-second-0123456789')
    assert first != f'prompt-sa-{AGENT}'
    assert first.startswith(f'prompt-sa-{AGENT}-')


def test_a_later_turn_without_a_uuid_is_dropped():
    """No identity to key on. Falling back to the bare id would file the
    resume's text as the LAUNCH prompt and the FIRST card would show it —
    losing the turn is the better failure."""
    assert _prompt_span_id(AGENT, 1, None) is None
    assert _prompt_span_id(AGENT, 1, '') is None
