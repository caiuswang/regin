"""The whole-session view of background shells, independent of the page.

`merge.py` joins a launch to its completion inside ONE window. A shell that
has run for half an hour has scrolled out of that window, which is exactly
the shell a reader needs to see — so the roster is a separate whole-session
aggregate, the shell equivalent of `agent_roster`.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from lib.trace.background_shells import (
    output_path, shell_roster, tasks_dir, terminal_outcome,
)

TASKS = '/private/tmp/claude-501/-Users-taowang-regin/PARENT/tasks'


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.execute("""CREATE TABLE session_spans (
        id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT, span_id TEXT,
        tool_use_id TEXT, name TEXT, start_time TEXT, status_code TEXT,
        attributes TEXT)""")
    return c


def _add(conn, name, attrs, *, trace='t', span_id='s', start='2026-09-19T11:00:00',
         status='OK', tool_use_id='tu'):
    conn.execute(
        'INSERT INTO session_spans (trace_id, span_id, tool_use_id, name,'
        ' start_time, status_code, attributes) VALUES (?,?,?,?,?,?,?)',
        (trace, span_id, tool_use_id, name, start, status, json.dumps(attrs)))


def _launch(conn, task_id, **kw):
    _add(conn, 'tool.Bash', {
        'tool_name': 'Bash', 'background_task_id': task_id,
        'command_preview': f'run {task_id}',
        'command_description': f'do {task_id}', **kw.pop('attrs', {})}, **kw)


def _done(conn, task_id, status='completed', output_file=None, **kw):
    _add(conn, 'task.notification', {
        'task_id': task_id, 'status': status, 'summary': f'{task_id} {status}',
        'output_file': output_file if output_file is not None
        else f'{TASKS}/{task_id}.output'}, **kw)


def test_a_launch_with_no_completion_is_running(conn):
    _launch(conn, 'bo58vwcaw', span_id='b1')

    [entry] = shell_roster(conn, ['t'])
    assert entry['status'] == 'running'
    assert entry['command'] == 'run bo58vwcaw'
    assert entry['description'] == 'do bo58vwcaw'
    assert entry['ended_at'] is None


def test_a_completed_shell_carries_its_outcome(conn):
    _launch(conn, 'bo58vwcaw', span_id='b1')
    _done(conn, 'bo58vwcaw', start='2026-09-19T11:00:30')

    [entry] = shell_roster(conn, ['t'])
    assert entry['status'] == 'completed'
    assert entry['ended_at'] == '2026-09-19T11:00:30'


def test_the_pending_and_resolved_rows_are_one_shell(conn):
    """The store is append-only: PreToolUse and PostToolUse each wrote a row
    for the same call, and the roster must not list the shell twice."""
    _launch(conn, 'bo58vwcaw', span_id='pending-tu', status='PENDING')
    _launch(conn, 'bo58vwcaw', span_id='b1')

    assert len(shell_roster(conn, ['t'])) == 1


def test_shells_of_other_sessions_are_not_listed(conn):
    _launch(conn, 'mine', span_id='b1')
    _launch(conn, 'theirs', span_id='b2', trace='other')

    assert [e['task_id'] for e in shell_roster(conn, ['t'])] == ['mine']


def test_a_foreground_bash_is_not_a_shell(conn):
    _add(conn, 'tool.Bash', {'tool_name': 'Bash', 'command_preview': 'ls'})

    assert shell_roster(conn, ['t']) == []


def test_the_output_directory_is_read_off_a_stated_path(conn):
    _done(conn, 'earlier')

    assert tasks_dir(conn, ['t']) == TASKS


def test_the_newest_stated_directory_wins(conn):
    """The directory is named for the session owning the harness process, and
    a resume replaces it mid-trace — on a real session the oldest notification
    named a directory that no longer existed."""
    _done(conn, 'old', output_file='/tmp/OLD-PARENT/tasks/old.output',
          start='2026-09-19T11:00:00')
    _done(conn, 'new', output_file='/tmp/NEW-PARENT/tasks/new.output',
          start='2026-09-19T11:30:00')

    assert tasks_dir(conn, ['t']) == '/tmp/NEW-PARENT/tasks' 


def test_a_running_shell_borrows_the_directory_a_sibling_stated(conn, tmp_path):
    """The running shell's own path is never stated anywhere a hook can see;
    every task of one session writes into the same directory, so a completed
    sibling locates it."""
    sibling = tmp_path / 'earlier.output'
    sibling.write_text('x')
    live = tmp_path / 'bo58vwcaw.output'
    live.write_text('step 1\n')
    _done(conn, 'earlier', output_file=str(sibling))
    _launch(conn, 'bo58vwcaw', span_id='b1')

    assert output_path(conn, ['t'], 'bo58vwcaw') == str(live)


def test_no_sibling_means_no_path_rather_than_a_guess(conn):
    _launch(conn, 'bo58vwcaw', span_id='b1')

    assert output_path(conn, ['t'], 'bo58vwcaw') == ''


def test_a_stated_path_naming_another_task_is_refused(conn):
    """A wrong directory must yield nothing, never another task's output."""
    _launch(conn, 'bo58vwcaw', span_id='b1')
    _done(conn, 'bo58vwcaw', output_file=f'{TASKS}/someone-else.output')

    assert output_path(conn, ['t'], 'bo58vwcaw') == ''


def test_a_missing_file_is_not_reported_as_a_path(conn, tmp_path):
    sibling = tmp_path / 'earlier.output'
    sibling.write_text('x')
    _done(conn, 'earlier', output_file=str(sibling))
    _launch(conn, 'gone', span_id='b1')

    assert output_path(conn, ['t'], 'gone') == ''


def test_an_ended_session_has_no_running_shells(conn):
    """A shell cannot outlive the process that launched it. Once the session
    is over, a launch with no completion is a shell that stopped without
    reporting — never one still running, which is what left two dead shells
    ticking upward at 9m31s in the foot-of-feed block."""
    _launch(conn, 'orphan', span_id='b1')

    [entry] = shell_roster(conn, ['t'], session_ended=True)
    assert entry['status'] == 'stopped'


def test_an_ended_session_keeps_a_real_outcome(conn):
    _launch(conn, 'done', span_id='b1')
    _done(conn, 'done', status='completed')

    [entry] = shell_roster(conn, ['t'], session_ended=True)
    assert entry['status'] == 'completed'


def test_a_live_session_still_reports_running(conn):
    _launch(conn, 'live', span_id='b1')

    [entry] = shell_roster(conn, ['t'], session_ended=False)
    assert entry['status'] == 'running'


def test_a_lost_notification_is_settled_by_output_file_marker(conn, tmp_path):
    """When the Notification hook is lost, the output file's harness marker
    is the ground truth and the roster must fall back to it — otherwise the
    UI keeps counting finished shells as running."""
    sibling = tmp_path / 'earlier.output'
    sibling.write_text('x')
    live = tmp_path / 'bo58vwcaw.output'
    live.write_text('step 1\n\n[exited with code 0]\n')
    _done(conn, 'earlier', output_file=str(sibling))
    _launch(conn, 'bo58vwcaw', span_id='b1')

    [entry] = shell_roster(conn, ['t'])
    assert entry['status'] == 'completed'
    assert entry['summary'] == 'Shell exited with code 0'
    assert entry['ended_at'] is not None


def test_a_running_shell_without_a_marker_stays_running(conn, tmp_path):
    """A shell whose output file exists but hasn't been closed yet is still
    running; the fallback must not over-settle."""
    sibling = tmp_path / 'earlier.output'
    sibling.write_text('x')
    live = tmp_path / 'bo58vwcaw.output'
    live.write_text('still writing\n')
    _done(conn, 'earlier', output_file=str(sibling))
    _launch(conn, 'bo58vwcaw', span_id='b1')

    [entry] = shell_roster(conn, ['t'])
    assert entry['status'] == 'running'
    assert entry['ended_at'] is None


def _output(tmp_path, text):
    path = tmp_path / 'bo58vwcaw.output'
    path.write_text(text)
    return str(path)


def test_a_killed_shell_is_read_off_its_own_output_file(tmp_path):
    """The harness appends its last word to the output file and tells nobody
    else — two thirds of the corpus's shells never got a notification at all,
    and a killed one never does."""
    outcome = terminal_outcome(_output(tmp_path, 'step 1\nstep 2\n\n[killed]\n'))

    assert outcome['status'] == 'killed'
    assert outcome['summary'] == 'Shell killed'
    assert outcome['ended_at']


def test_a_clean_exit_reads_completed(tmp_path):
    outcome = terminal_outcome(_output(tmp_path, 'done\n\n[exited with code 0]\n'))

    assert outcome['status'] == 'completed'
    assert outcome['summary'] == 'Shell exited with code 0'


def test_a_nonzero_exit_reads_failed(tmp_path):
    outcome = terminal_outcome(_output(tmp_path, 'boom\n\n[exited with code 1]\n'))

    assert outcome['status'] == 'failed'


def test_a_file_with_no_marker_settles_nothing(tmp_path):
    assert terminal_outcome(_output(tmp_path, 'still writing\n')) is None


def test_a_marker_the_command_printed_settles_nothing(tmp_path):
    """The marker is the harness's LAST line. A shell that merely prints the
    phrase and keeps running is still running."""
    assert terminal_outcome(_output(tmp_path, '[killed]\nand on we go\n')) is None


def test_a_missing_file_settles_nothing(tmp_path):
    assert terminal_outcome(str(tmp_path / 'gone.output')) is None


def test_a_long_file_is_read_from_its_tail(tmp_path):
    assert terminal_outcome(
        _output(tmp_path, 'x' * 100_000 + '\n[killed]\n'))['status'] == 'killed'
