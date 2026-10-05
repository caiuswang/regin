"""The background shells a session started, and where their output is.

A `Bash` call made with `run_in_background` hands control back in
milliseconds and its shell keeps running out of sight. `merge.py` joins each
launch to its completion for the conversation card, but that join only sees
ONE PAGE of spans — and a shell that has been running for half an hour has
long since scrolled out of it. This is the whole-session aggregate instead,
the shell equivalent of `agent_roster`: it answers "what is still running"
independently of what the reader happens to have loaded.

Output files are a separate problem. The harness names the path in the
`tool_result` PROSE ("Output is being written to: …"), which no hook sees,
and the directory is keyed by the PARENT session id — so it can neither be
read off the launch nor reconstructed from this session's id (the mistake
`lib/session_probe.py` warns about). What CAN be trusted is the path a
completion notification states outright: every task of one session writes
into the same directory, so one observed `output_file` locates the rest, and
the file is only ever used after its name is confirmed to be the task's own.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

_LAUNCH_SQL = """
    SELECT span_id, tool_use_id, start_time, status_code, attributes
      FROM session_spans
     WHERE trace_id IN ({ph})
       AND name = 'tool.Bash'
       AND json_extract(attributes, '$.background_task_id') IS NOT NULL
     ORDER BY id
"""

_NOTIFICATION_SQL = """
    SELECT start_time, attributes
      FROM session_spans
     WHERE trace_id IN ({ph})
       AND name = 'task.notification'
     ORDER BY id
"""

RUNNING = 'running'

#: The harness's own last word on a shell, appended to its output file when
#: the shell dies: `[killed]` for a KillShell or a teardown, the exit code for
#: a shell that ran to its end. Matched as a whole line, so the phrase inside
#: a line of a command's own output settles nothing — a live command whose
#: LAST line is exactly the marker is the one ambiguity this format leaves.
_TERMINAL_RE = re.compile(r'^\[(?:killed|exited with code (\d+))\]$')

_TAIL_BYTES = 256


def terminal_outcome(path: str) -> dict | None:
    """`{status, summary, ended_at}` when the file ends in the harness's
    end-of-shell marker, else None.

    The third source of a completion, and for most shells the only one: two
    thirds of the corpus's background shells never got a `task.notification`
    at all, and a killed one is reported to nobody. None means nothing said so
    — a shell still writing and a file that is gone are both left `running`
    rather than guessed at.
    """
    try:
        with open(path, 'rb') as fh:
            stat = os.fstat(fh.fileno())
            if stat.st_size > _TAIL_BYTES:
                fh.seek(stat.st_size - _TAIL_BYTES)
            tail = fh.read().decode('utf-8', 'replace')
    except OSError:
        return None
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    match = _TERMINAL_RE.match(lines[-1]) if lines else None
    if not match:
        return None
    code = match.group(1)
    return {
        'status': _exit_status(code) if code is not None else 'killed',
        'summary': f'Shell {lines[-1][1:-1]}',
        'ended_at': datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


def _exit_status(code: str) -> str:
    return 'completed' if code == '0' else 'failed'


def _rows(conn, sql: str, trace_ids) -> list:
    ph = ','.join('?' * len(trace_ids))
    return conn.execute(sql.format(ph=ph), tuple(trace_ids)).fetchall()


def _attrs(raw) -> dict:
    try:
        value = json.loads(raw or '{}')
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _completions(conn, trace_ids) -> dict:
    """`{task_id: {status, summary, output_file, ended_at}}`.

    One envelope can settle SEVERAL tasks — that is how the harness reports
    shells orphaned by a session that ended — so every id it names is read,
    not just the one that keys the span.
    """
    out: dict = {}
    for start_time, raw in _rows(conn, _NOTIFICATION_SQL, trace_ids):
        a = _attrs(raw)
        ids = a.get('task_ids')
        if not isinstance(ids, list) or not ids:
            ids = [a.get('task_id')]
        for task_id in ids:
            if isinstance(task_id, str) and task_id:
                out[task_id] = {'status': a.get('status') or RUNNING,
                                'summary': a.get('summary') or '',
                                'output_file': a.get('output_file') or '',
                                'ended_at': start_time}
    return out


def tasks_dir(conn, trace_ids) -> str:
    """The directory this session's task output files live in, or ''.

    Read off whatever completion notification already stated one — never
    assembled from a session id or a directory-layout convention.

    The NEWEST such statement wins. The directory is named for the session
    that owns the harness process, which a `/clear` or a resume replaces
    mid-trace: on a real session the oldest notification named a directory
    that no longer exists while later tasks were writing into a new one.
    """
    newest = ''
    for _, raw in _rows(conn, _NOTIFICATION_SQL, trace_ids):
        path = _attrs(raw).get('output_file')
        if isinstance(path, str) and path:
            newest = os.path.dirname(path)
    return newest


def output_path(conn, trace_ids, task_id: str) -> str:
    """The output file for `task_id`, or '' when this session has not yet
    stated a directory. Confirmed to be named for the task, so a wrong
    directory yields nothing rather than another task's output."""
    if not task_id:
        return ''
    stated = _completions(conn, trace_ids).get(task_id, {}).get('output_file')
    if isinstance(stated, str) and stated:
        return stated if os.path.basename(stated) == f'{task_id}.output' else ''
    directory = tasks_dir(conn, trace_ids)
    if not directory:
        return ''
    guess = os.path.join(directory, f'{task_id}.output')
    return guess if os.path.exists(guess) else ''


def has_unreported_shells(conn, trace_ids) -> bool:
    """Does this session hold a shell nothing has reported the end of?

    The gate for the notification backfill: a lost notification shows up
    exactly here, as a launch that never settles. False costs two indexed
    reads and skips the transcript walk entirely.
    """
    return any(e['status'] == RUNNING for e in shell_roster(conn, trace_ids))


def _entry(row, attrs: dict, task_id: str, end: dict) -> dict:
    span_id, tool_use_id, start_time = row[0], row[1], row[2]
    return {
        'task_id': task_id,
        'span_id': span_id,
        'tool_use_id': tool_use_id,
        'command': attrs.get('command') or attrs.get('command_preview') or '',
        'description': attrs.get('command_description') or '',
        'started_at': start_time,
        'status': end.get('status') or RUNNING,
        'summary': end.get('summary') or '',
        'ended_at': end.get('ended_at') or None,
    }


#: A shell known to have ended with no word on how: a launch with no
#: completion on a session that is over, or one the harness's `Stop` snapshot
#: of in-flight tasks no longer lists. Not `running`: a shell is a child of the
#: process that launched it, so once that process is gone the shell is too —
#: whether the teardown killed it or it was orphaned. The harness reports
#: orphans to the NEXT session, which may be a different trace entirely, so
#: this session's own rows can never say so themselves.
STOPPED = 'stopped'


def shell_roster(conn, trace_ids, session_ended: bool = False) -> list[dict]:
    """Every background shell this session started, oldest first.

    One entry per task id: the append-only store holds a pending and a
    resolved row for the same call, and they are one shell.

    `session_ended` settles the launches nothing ever reported on. A real
    outcome always wins — an ended session does not rewrite a shell that
    already said how it finished.
    """
    done = _completions(conn, trace_ids)
    by_task: dict = {}
    for row in _rows(conn, _LAUNCH_SQL, trace_ids):
        attrs = _attrs(row[4])
        task_id = attrs.get('background_task_id')
        if isinstance(task_id, str) and task_id:
            by_task[task_id] = _entry(row, attrs, task_id,
                                      done.get(task_id) or {})
    entries = sorted(by_task.values(), key=lambda e: e['started_at'] or '')
    if not session_ended:
        return entries
    return [e if e['status'] != RUNNING else {**e, 'status': STOPPED}
            for e in entries]
