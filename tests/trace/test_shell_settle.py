"""A background shell settles on what the harness states, not on a guess.

`Stop` hands the hook the session's in-flight background tasks; a shell the
roster still calls running that the snapshot omits has ended. A successful
`TaskStop` on a shell names the kill outright. Both write the shared
`task.notification` span id, and neither overwrites a real outcome.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import shell_lifecycle
from hook_manager.handlers.post_tool_builders import _TOOL_BUILDERS
from lib.trace.task_notification import notification_span_id


@pytest.fixture
def posted(monkeypatch):
    spans = []
    monkeypatch.setattr('lib.hook_plugin.post_span',
                        lambda **kw: spans.append(kw) or True)
    return spans


@pytest.fixture
def trace_db(tmp_path, monkeypatch):
    from lib.orm import engine

    path = str(tmp_path / 'spans.db')
    with sqlite3.connect(path) as setup:
        setup.execute("""CREATE TABLE session_spans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT, span_id TEXT,
            tool_use_id TEXT, name TEXT, start_time TEXT, status_code TEXT,
            attributes TEXT)""")
    monkeypatch.setattr(engine, 'get_connection', lambda: sqlite3.connect(path))

    def add(name, attrs, trace='t'):
        with sqlite3.connect(path) as conn:
            conn.execute(
                'INSERT INTO session_spans (trace_id, span_id, name,'
                ' start_time, status_code, attributes) VALUES (?,?,?,?,?,?)',
                (trace, f'{name}-{attrs.get("task_id") or attrs.get("background_task_id")}',
                 name, '2026-10-05T11:00:00', 'OK', json.dumps(attrs)))
    return add


def _launch(add, task_id, trace='t'):
    add('tool.Bash', {'background_task_id': task_id, 'command_preview': 'sleep 900'},
        trace=trace)


def _stop(background_tasks, session='t'):
    raw = {'hook_event_name': 'Stop', 'session_id': session}
    if background_tasks is not None:
        raw['background_tasks'] = background_tasks
    return HookPayload(event='Stop', session_id=session, raw=raw)


def _in_flight(task_id):
    return {'id': task_id, 'type': 'shell', 'status': 'running',
            'description': 'wait', 'command': 'sleep 900'}


def test_a_shell_the_snapshot_omits_is_settled(posted, trace_db):
    _launch(trace_db, 'bgone0001')

    shell_lifecycle.handle_stop(_stop([]))

    [span] = posted
    assert span['span_id'] == notification_span_id('bgone0001')
    assert span['name'] == 'task.notification'
    assert span['attributes']['status'] == 'stopped'
    assert span['attributes']['task_ids'] == ['bgone0001']


def test_a_shell_the_snapshot_lists_stays_running(posted, trace_db):
    _launch(trace_db, 'blive0001')
    _launch(trace_db, 'bgone0001')

    shell_lifecycle.handle_stop(_stop([_in_flight('blive0001')]))

    assert [s['attributes']['task_id'] for s in posted] == ['bgone0001']


def test_no_snapshot_field_settles_nothing(posted, trace_db):
    """Kimi and older Claude Code fire Stop without `background_tasks`;
    absence is not an empty in-flight set."""
    _launch(trace_db, 'blive0001')

    shell_lifecycle.handle_stop(_stop(None))

    assert posted == []


def test_the_marker_supplies_the_reason_when_the_file_is_readable(
        tmp_path, posted, trace_db):
    out = tmp_path / 'bgone0001.output'
    out.write_text('building\n\n[exited with code 2]\n')
    _launch(trace_db, 'bgone0001')
    trace_db('task.notification', {'task_id': 'sibling', 'status': 'completed',
                                   'output_file': str(tmp_path / 'sibling.output')})

    shell_lifecycle.handle_stop(_stop([]))

    [span] = posted
    assert span['attributes']['status'] == 'failed'
    assert span['attributes']['output_file'] == str(out)


def test_a_shell_that_already_reported_is_not_rewritten(posted, trace_db):
    _launch(trace_db, 'bdone0001')
    trace_db('task.notification', {'task_id': 'bdone0001', 'status': 'completed',
                                   'output_file': ''})

    shell_lifecycle.handle_stop(_stop([]))

    assert posted == []


def test_a_refused_post_stops_the_sweep(monkeypatch, trace_db):
    """A server that is down must not cost one full retry budget per shell
    inside the Stop hook."""
    from lib.trace import repair

    calls = []
    monkeypatch.setattr('lib.hook_plugin.post_span',
                        lambda **kw: calls.append(kw) and False)
    for i in range(3):
        _launch(trace_db, f'bgone000{i}')

    assert repair.settle_shells_out_of_flight('t', set()) == 0
    assert len(calls) == 1


def test_another_sessions_shells_are_untouched(posted, trace_db):
    _launch(trace_db, 'btheirs01', trace='other')

    shell_lifecycle.handle_stop(_stop([]))

    assert posted == []


def _task_stop(task_id, task_type='local_bash', session='t'):
    return HookPayload(
        event='PostToolUse', session_id=session, tool_name='TaskStop',
        tool_input={'task_id': task_id},
        tool_response={'message': f'Successfully stopped task: {task_id}',
                       'task_id': task_id, 'task_type': task_type})


def test_task_stop_settles_a_running_shell_as_killed(posted, trace_db):
    _launch(trace_db, 'bkill0001')

    shell_lifecycle.handle_task_stop(_task_stop('bkill0001'))

    [span] = posted
    assert span['span_id'] == notification_span_id('bkill0001')
    assert span['attributes']['status'] == 'killed'


def test_task_stop_on_a_subagent_writes_nothing(posted, trace_db):
    shell_lifecycle.handle_task_stop(_task_stop('a35b5c4afae0', task_type='local_agent'))

    assert posted == []


def test_task_stop_on_a_settled_shell_writes_nothing(posted, trace_db):
    _launch(trace_db, 'bdone0001')
    trace_db('task.notification', {'task_id': 'bdone0001', 'status': 'completed',
                                   'output_file': ''})

    shell_lifecycle.handle_task_stop(_task_stop('bdone0001'))

    assert posted == []


def test_the_task_stop_span_carries_its_task_id():
    attrs: dict = {}
    _TOOL_BUILDERS['TaskStop'](attrs, {'task_id': 'bkill0001'},
                               {'task_id': 'bkill0001', 'task_type': 'local_bash'},
                               None)

    assert attrs == {'task_id': 'bkill0001', 'task_type': 'local_bash'}


def test_both_handlers_are_registered():
    from hook_manager.registry import REGISTRY

    by_name = {h.name: h for h in REGISTRY}
    assert by_name['shell_settle_stop'].events == ['Stop']
    task_stop = by_name['shell_settle_task_stop']
    assert task_stop.predicate(_task_stop('x'))
    assert not task_stop.predicate(HookPayload(event='PostToolUse', tool_name='Bash'))
