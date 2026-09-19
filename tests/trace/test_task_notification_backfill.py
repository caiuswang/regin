"""A task notification the hook never saw must still reach the trace.

`task.notification` spans had exactly ONE writer: the UserPromptSubmit hook.
A notification injected as a queued item when a session resumes does not fire
that hook, so the fact was lost for good — which is how two shells the harness
had already killed kept reporting `running`, elapsed ticking upward, on a
session that was still very much alive.

The transcript holds the entry either way, so it is the second writer — the
same hook/transcript pairing every other span family already has.
"""

from __future__ import annotations

import json

import pytest

from lib.trace import repair
from lib.trace.task_notification import notification_span_id

ORPHAN_TEXT = (
    '<task-notification>\n'
    '<task-id>bmbknevn9</task-id>\n'
    '<task-id>bsk9mt0d6</task-id>\n'
    '<task-id>__orphan_summary__:shell</task-id>\n'
    '<status>stopped</status>\n'
    '<summary>2 background shell command tasks did not finish.</summary>\n'
    '</task-notification>'
)


def _transcript(tmp_path, *texts):
    path = tmp_path / 't.jsonl'
    with path.open('w') as fh:
        for i, text in enumerate(texts):
            fh.write(json.dumps({
                'type': 'user', 'uuid': f'u{i}',
                'timestamp': '2026-09-19T12:14:00.000Z',
                'message': {'role': 'user', 'content': text},
            }) + '\n')
    return str(path)


@pytest.fixture
def posted(monkeypatch):
    spans = []
    monkeypatch.setattr('lib.hook_plugin.post_span',
                        lambda **kw: spans.append(kw) or True)
    return spans


def test_the_span_id_matches_what_the_hook_would_have_written():
    """Both writers must land on ONE row; the store upserts on (trace, span)."""
    assert notification_span_id('bmbknevn9') == 'task-bmbknevn9'


def test_an_unseen_notification_is_backfilled(tmp_path, posted, monkeypatch):
    monkeypatch.setattr(repair, '_existing_notification_span_ids', lambda t: set())

    n = repair.backfill_task_notifications('t', _transcript(tmp_path, ORPHAN_TEXT))

    assert n == 1
    assert posted[0]['name'] == 'task.notification'
    assert posted[0]['attributes']['status'] == 'stopped'
    assert posted[0]['attributes']['task_ids'] == ['bmbknevn9', 'bsk9mt0d6']


def test_a_notification_already_in_the_trace_is_not_rewritten(tmp_path, posted, monkeypatch):
    monkeypatch.setattr(repair, '_existing_notification_span_ids',
                        lambda t: {'task-bmbknevn9'})

    n = repair.backfill_task_notifications('t', _transcript(tmp_path, ORPHAN_TEXT))

    assert n == 0
    assert posted == []


def test_an_ordinary_prompt_is_not_a_notification(tmp_path, posted, monkeypatch):
    monkeypatch.setattr(repair, '_existing_notification_span_ids', lambda t: set())

    n = repair.backfill_task_notifications(
        't', _transcript(tmp_path, 'please fix the background shell block'))

    assert n == 0
    assert posted == []


@pytest.fixture
def trace_db(tmp_path, monkeypatch):
    """A trace whose spans the backfill reads through the real connection."""
    import sqlite3

    from lib.orm import engine

    path = str(tmp_path / 'spans.db')
    with sqlite3.connect(path) as setup:
        setup.execute("""CREATE TABLE session_spans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT, span_id TEXT,
            tool_use_id TEXT, name TEXT, start_time TEXT, status_code TEXT,
            attributes TEXT)""")
    monkeypatch.setattr(engine, 'get_connection', lambda: sqlite3.connect(path))

    def add(name, attrs):
        with sqlite3.connect(path) as conn:
            conn.execute(
                'INSERT INTO session_spans (trace_id, span_id, name,'
                ' start_time, status_code, attributes) VALUES (?,?,?,?,?,?)',
                ('t', f'{name}-{attrs.get("task_id") or attrs.get("background_task_id")}',
                 name, '2026-09-19T11:00:00', 'OK', json.dumps(attrs)))
    return add


def _shell(add, tmp_path, task_id, text):
    out = tmp_path / f'{task_id}.output'
    out.write_text(text)
    add('tool.Bash', {'background_task_id': task_id, 'command_preview': 'sleep 900'})
    add('task.notification', {'task_id': 'sibling', 'status': 'completed',
                              'output_file': str(tmp_path / 'sibling.output')})
    return str(out)


def test_a_killed_shell_is_settled_from_its_output_file(tmp_path, posted, trace_db):
    """Nobody is ever told a shell was killed — the marker in its own output
    file is the only record, and without it the roster counts it running
    forever."""
    out = _shell(trace_db, tmp_path, 'b3wzeajk7', 'waiting\n\n[killed]\n')

    assert repair.backfill_killed_shell_notifications('t') == 1
    [span] = posted
    assert span['span_id'] == notification_span_id('b3wzeajk7')
    assert span['name'] == 'task.notification'
    assert span['attributes']['status'] == 'killed'
    assert span['attributes']['task_ids'] == ['b3wzeajk7']
    assert span['attributes']['output_file'] == out


def test_a_shell_still_writing_is_left_running(tmp_path, posted, trace_db):
    _shell(trace_db, tmp_path, 'b3wzeajk7', 'still going\n')

    assert repair.backfill_killed_shell_notifications('t') == 0
    assert posted == []


def test_a_shell_that_already_reported_is_not_rewritten(tmp_path, posted, trace_db):
    """A real outcome always wins; only shells the roster still calls running
    are read."""
    _shell(trace_db, tmp_path, 'b3wzeajk7', 'waiting\n\n[killed]\n')
    trace_db('task.notification', {'task_id': 'b3wzeajk7', 'status': 'completed',
                                   'output_file': str(tmp_path / 'b3wzeajk7.output')})

    assert repair.backfill_killed_shell_notifications('t') == 0
    assert posted == []
