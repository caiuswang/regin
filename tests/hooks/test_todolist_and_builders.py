"""Kimi-native tool spans must carry the same attrs the cards read.

Every payload below is copied from a real capture (`~/.kimi-code/
hook-payloads.jsonl`, plus a `WebSearch` call off a session `wire.jsonl`).
Before the builders existed, a Kimi `TodoList`/`FetchURL` span stored nothing
but `{tool_name, tool_use_id, agent_type}` — no task list, no url — so the
TASK LIST card, the header `tasks N/M` badge and the WebFetch card were all
dead. Claude payloads must keep their existing attrs unchanged.
"""

from __future__ import annotations

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import post_tool_trace
from lib import hook_plugin


@pytest.fixture
def _captured(monkeypatch):
    spans: list[dict] = []
    monkeypatch.setattr(hook_plugin, 'post_span', lambda **kw: spans.append(kw))
    return spans


def _kimi_payload(tool_name, tool_input, output='ok', cwd='/Users/taowang/regin'):
    return HookPayload.from_stdin_json('PostToolUse', {
        'hook_event_name': 'PostToolUse',
        'agent_type': 'kimi',
        'session_id': 'session_todo',
        'cwd': cwd,
        'tool_name': tool_name,
        'tool_input': tool_input,
        'tool_call_id': 'tool_todo_1',
        'tool_use_id': 'tool_todo_1',
        'tool_response': {'output': output},
    })


# Verbatim `tool_input` of a captured Kimi TodoList PostToolUse payload.
_KIMI_TODOS = {
    'todos': [
        {'title': 'Add provider metadata to skills API responses',
         'status': 'in_progress'},
        {'title': 'Make skills Vue views provider-aware', 'status': 'pending'},
        {'title': 'Update stale Claude-specific docstrings', 'status': 'done'},
    ],
}


def test_kimi_todolist_span_carries_the_task_snapshot(_captured):
    post_tool_trace._emit_span(_kimi_payload('TodoList', _KIMI_TODOS))

    attrs = _captured[0]['attributes']
    assert _captured[0]['name'] == 'tool.TodoList'
    assert attrs['todos'] == [
        {'task_id': '1',
         'subject': 'Add provider metadata to skills API responses',
         'status': 'in_progress'},
        {'task_id': '2', 'subject': 'Make skills Vue views provider-aware',
         'status': 'pending'},
        # Kimi says `done`; the task-list surfaces render `completed`.
        {'task_id': '3', 'subject': 'Update stale Claude-specific docstrings',
         'status': 'completed'},
    ]


def test_claude_todowrite_stores_no_snapshot(_captured):
    # Claude's main agent and each of its subagents write independent TodoWrite
    # lists into ONE trace_id, so a `todos` attr would feed them all into the
    # single snapshot fold and let each retire the others' live tasks.
    payload = HookPayload.from_stdin_json('PostToolUse', {
        'hook_event_name': 'PostToolUse',
        'session_id': 'sess-claude',
        'tool_name': 'TodoWrite',
        'tool_input': {'todos': [
            {'content': 'Ship it', 'status': 'completed',
             'activeForm': 'Shipping it'},
        ]},
        'tool_response': {},
    })
    post_tool_trace._emit_span(payload)

    assert _captured[0]['name'] == 'tool.TodoWrite'
    assert 'todos' not in _captured[0]['attributes']


def test_todolist_unknown_status_degrades_to_pending(_captured):
    post_tool_trace._emit_span(_kimi_payload(
        'TodoList', {'todos': [{'title': 'x', 'status': 'weird'},
                               {'title': 'y'}]}))
    assert [t['status'] for t in _captured[0]['attributes']['todos']] == [
        'pending', 'pending']


def test_todolist_without_todos_adds_no_attr(_captured):
    post_tool_trace._emit_span(_kimi_payload('TodoList', {}))
    assert 'todos' not in _captured[0]['attributes']


def test_kimi_fetchurl_span_carries_url_and_prompt(_captured):
    post_tool_trace._emit_span(_kimi_payload('FetchURL', {
        'url': 'https://www.kimi.com/code/docs/en/kimi-code-cli/'
               'customization/skills.html',
        'prompt': 'how are skills discovered',
    }))

    attrs = _captured[0]['attributes']
    assert _captured[0]['name'] == 'tool.FetchURL'
    assert attrs['url'] == ('https://www.kimi.com/code/docs/en/kimi-code-cli/'
                            'customization/skills.html')
    assert attrs['fetch_prompt'] == 'how are skills discovered'


def test_kimi_websearch_span_carries_query(_captured):
    # Kimi's WebSearch args are `{query, limit, include_content}` — the card
    # reads `query`, which the shared WebSearch builder already stores.
    post_tool_trace._emit_span(_kimi_payload('WebSearch', {
        'query': 'kimi code cli hooks documentation session transcript',
        'limit': 10, 'include_content': True,
    }))
    assert _captured[0]['attributes']['query'] == (
        'kimi code cli hooks documentation session transcript')


def test_kimi_relative_path_is_absolutized_against_cwd(_captured):
    post_tool_trace._emit_span(_kimi_payload(
        'Edit', {'path': 'web/blueprints/skills.py',
                 'old_string': 'a', 'new_string': 'b'}))
    assert _captured[0]['attributes']['file_path'] == (
        '/Users/taowang/regin/web/blueprints/skills.py')


def test_absolute_path_is_left_untouched(_captured):
    post_tool_trace._emit_span(_kimi_payload(
        'Read', {'path': '/Users/taowang/regin/README.md'}))
    assert _captured[0]['attributes']['file_path'] == (
        '/Users/taowang/regin/README.md')


def _claude_payload(tool_name, tool_input, cwd='/Users/taowang/regin'):
    return HookPayload.from_stdin_json('PostToolUse', {
        'hook_event_name': 'PostToolUse',
        'session_id': 'sess-claude',
        'cwd': cwd,
        'tool_name': tool_name,
        'tool_input': tool_input,
        'tool_response': {},
    })


@pytest.mark.parametrize('tool_input, expected', [
    ({'file_path': 'lib/topics/proposals.py'}, 'lib/topics/proposals.py'),
    ({'path': 'hook_manager'}, 'hook_manager'),
    ({'file_path': '/tmp/a.py'}, '/tmp/a.py'),
])
def test_claude_file_path_is_stored_verbatim(_captured, tool_input, expected):
    # Claude sends relative tool paths routinely — 13,109 of the 64,259
    # file_path spans on record, mostly `Read`. Anchoring those against cwd
    # would file new spans under a different identity than every span already
    # stored and split a file's history in two, so cwd-anchoring is Kimi-only.
    post_tool_trace._emit_span(_claude_payload('Read', tool_input))
    assert _captured[0]['attributes']['file_path'] == expected
