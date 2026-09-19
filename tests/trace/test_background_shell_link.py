"""A backgrounded Bash call must read as a background shell, not a 3 ms success.

`run_in_background` hands control back in milliseconds; the completion arrives
much later as a `task.notification` span with its own `task_id`. Before these
tests the two rows were never joined, so the trace showed an instant no-output
success and an unrelated notification elsewhere, and a shell still running left
no state at all.
"""

from __future__ import annotations

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import post_tool_trace, pre_tool_trace
from lib import hook_plugin
from lib.trace.merge import merge_spans


@pytest.fixture
def _captured(monkeypatch):
    spans: list[dict] = []
    monkeypatch.setattr(hook_plugin, 'post_span', lambda **kw: spans.append(kw))
    return spans


def _post_payload(tool_input, tool_response):
    return HookPayload.from_stdin_json('PostToolUse', {
        'hook_event_name': 'PostToolUse',
        'agent_type': 'claude',
        'session_id': 'sess_bg',
        'cwd': '/Users/taowang/regin',
        'tool_name': 'Bash',
        'tool_use_id': 'toolu_bg1',
        'tool_input': tool_input,
        'tool_response': tool_response,
    })


def _pre_payload(tool_input):
    return HookPayload.from_stdin_json('PreToolUse', {
        'hook_event_name': 'PreToolUse',
        'agent_type': 'claude',
        'session_id': 'sess_bg',
        'cwd': '/Users/taowang/regin',
        'tool_name': 'Bash',
        'tool_use_id': 'toolu_bg1',
        'tool_input': tool_input,
    })


def test_pending_span_marks_the_declared_background_launch(_captured):
    """PreToolUse is the only event that exists when a lost PostToolUse leaves
    the pending row as the whole record of the call."""
    pre_tool_trace.handle(_pre_payload({
        'command': 'npm run dev',
        'description': 'Start the dev server',
        'run_in_background': True,
    }))

    attrs = _captured[0]['attributes']
    assert attrs['run_in_background'] is True
    assert attrs['command_description'] == 'Start the dev server'


def test_resolved_span_carries_intent_and_description(_captured):
    post_tool_trace._emit_span(_post_payload(
        {'command': 'npm run dev', 'description': 'Start the dev server',
         'run_in_background': True},
        {'stdout': '', 'stderr': '', 'backgroundTaskId': 'bo58vwcaw'},
    ))

    attrs = _captured[0]['attributes']
    assert attrs['run_in_background'] is True
    assert attrs['command_description'] == 'Start the dev server'
    assert attrs['background_task_id'] == 'bo58vwcaw'


def test_foreground_bash_gains_no_background_keys(_captured):
    post_tool_trace._emit_span(_post_payload(
        {'command': 'ls', 'description': 'List files'},
        {'stdout': 'a\n', 'stderr': ''},
    ))

    attrs = _captured[0]['attributes']
    assert 'run_in_background' not in attrs
    assert 'background_task_id' not in attrs
    assert attrs['command_description'] == 'List files'


def _bash_span(**attrs):
    return {
        'id': 1, 'trace_id': 't', 'span_id': 'bash1', 'parent_id': None,
        'name': 'tool.Bash', 'status_code': 'OK',
        'start_time': '2026-09-19T11:17:26.100000',
        'end_time': '2026-09-19T11:17:26.103000', 'duration_ms': 3,
        'tool_use_id': 'toolu_bg1',
        'attributes': {'tool_name': 'Bash', 'command_preview': 'npm run dev',
                       **attrs},
    }


def _notification_span(span_id='task-bo58vwcaw', **attrs):
    return {
        'id': 2, 'trace_id': 't', 'span_id': span_id, 'parent_id': None,
        'name': 'task.notification', 'status_code': 'OK',
        'start_time': '2026-09-19T11:17:56.100000',
        'end_time': '2026-09-19T11:17:56.100000', 'duration_ms': 0,
        'attributes': {'text': '<task-notification>…</task-notification>',
                       **attrs},
    }


def _by_id(spans):
    return {s['span_id']: s for s in spans}


def test_a_shell_with_no_completion_reads_as_running():
    out = merge_spans([_bash_span(background_task_id='bo58vwcaw')])

    assert _by_id(out)['bash1']['attributes']['background_status'] == 'running'


def test_the_completion_is_joined_onto_the_launching_call():
    out = merge_spans([
        _bash_span(background_task_id='bo58vwcaw'),
        _notification_span(
            task_id='bo58vwcaw', tool_use_id='toolu_bg1', status='completed',
            summary='Background command "Start the dev server" completed (exit code 0)',
            output_file='/tmp/tasks/bo58vwcaw.output'),
    ])

    bash = _by_id(out)['bash1']
    a = bash['attributes']
    assert a['background_status'] == 'completed'
    assert a['background_elapsed_ms'] == 30000
    assert a['background_summary'].startswith('Background command')
    assert a['background_output_file'] == '/tmp/tasks/bo58vwcaw.output'
    assert a['background_ended_at'] == '2026-09-19T11:17:56.100000'
    # The tool call really did cost 3 ms; cost/stats aggregates read this.
    assert bash['duration_ms'] == 3


def test_a_failed_shell_keeps_the_harness_word():
    out = merge_spans([
        _bash_span(background_task_id='bo58vwcaw'),
        _notification_span(task_id='bo58vwcaw', status='failed',
                           summary='exit code 1'),
    ])

    assert _by_id(out)['bash1']['attributes']['background_status'] == 'failed'


def test_a_subagent_report_is_not_mistaken_for_a_shell():
    """An agent's completion carries the same envelope; its task_id matches no
    Bash span, so nothing may be stamped and the card must survive."""
    notif = _notification_span(span_id='task-a61cd076a1cd',
                               task_id='a61cd076a1cd4cae0',
                               tool_use_id='toolu_agent',
                               status='completed', result='Findings.')
    out = merge_spans([_bash_span(), notif])

    assert 'background_status' not in _by_id(out)['bash1']['attributes']
    assert _by_id(out)['task-a61cd076a1cd']['attributes']['result'] == 'Findings.'


def test_the_join_is_idempotent():
    raw = [
        _bash_span(background_task_id='bo58vwcaw'),
        _notification_span(task_id='bo58vwcaw', status='completed'),
    ]
    once = merge_spans(raw)

    assert merge_spans(once) == once
