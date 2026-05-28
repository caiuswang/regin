"""Shared trace context for Claude Code hooks.

Maintains a per-session active span stack in a JSON file so that
independent hook processes can read/write the current trace context.

Concurrency
-----------
Read-modify-write operations on the span stack are protected by an
advisory file lock on a sidecar `{session}.lock` file. All mutating
entry points (`start_span`, `end_span`, `pop_all`) acquire `LOCK_EX` on
the sidecar for the full RMW window, then replace the data file
atomically via `os.replace`. Concurrent hooks for the same session
serialize cleanly; hooks for different sessions do not contend.

Corrupt data files are surfaced to `~/.claude/traces/ingest-errors.jsonl`
rather than silently reset — otherwise a single bad write would orphan
every subsequent span for that session with no indication.

Typical usage:
    from lib.trace.trace_context import start_span, end_span, current_span, pop_all

    # On UserPromptSubmit
    start_span(session_id, 'prompt', {'text': 'hello'})

    # On PostToolUse (file edit)
    start_span(session_id, 'file.edit', {'file_path': '/a/b.java'})

    # On grit check (same PostToolUse batch)
    parent = current_span(session_id)
    # post rule.check with parent_id = parent['span_id']

    # On ExitPlanMode
    end_span(session_id, 'plan.enter')

    # On next UserPromptSubmit
    ended = pop_all(session_id)
"""

import contextlib
import json
import os
import fcntl
import uuid
from datetime import datetime

from lib.providers import get_active_provider

TRACE_DIR = str(get_active_provider().traces_dir())
_INGEST_ERROR_LOG = os.path.join(TRACE_DIR, 'ingest-errors.jsonl')


def _path(session_id: str) -> str:
    os.makedirs(TRACE_DIR, exist_ok=True)
    return os.path.join(TRACE_DIR, f"{session_id}.json")


def _lock_path(session_id: str) -> str:
    return _path(session_id) + '.lock'


def _log_corruption(session_id: str, exc: BaseException) -> None:
    """Best-effort log of a corrupt trace-context file."""
    try:
        os.makedirs(TRACE_DIR, exist_ok=True)
        entry = {
            'timestamp': datetime.now().isoformat(),
            'endpoint': 'trace_context._read',
            'session_id': session_id,
            'error_type': type(exc).__name__,
            'error': str(exc),
        }
        with open(_INGEST_ERROR_LOG, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError:
        pass


@contextlib.contextmanager
def _locked(session_id: str):
    """Hold LOCK_EX on the sidecar lock file for the duration of the block."""
    lock_path = _lock_path(session_id)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _read_unlocked(session_id: str) -> dict:
    path = _path(session_id)
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {'stack': []}
    except (json.JSONDecodeError, ValueError) as exc:
        _log_corruption(session_id, exc)
        return {'stack': []}


def _write_unlocked(session_id: str, ctx: dict) -> None:
    path = _path(session_id)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(ctx, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def start_span(
    session_id: str,
    name: str,
    attributes: dict | None = None,
    persistent: bool = False,
    parent_id: str | None = None,
) -> dict:
    """Start a span and push it onto the active stack. Returns span dict."""
    with _locked(session_id):
        ctx = _read_unlocked(session_id)
        span_id = uuid.uuid4().hex[:16]
        resolved_parent_id = parent_id
        if resolved_parent_id is None and ctx['stack']:
            resolved_parent_id = ctx['stack'][-1]['span_id']
        span = {
            'span_id': span_id,
            'parent_id': resolved_parent_id,
            'name': name,
            'start_time': datetime.now().isoformat(),
            'attributes': attributes or {},
            'persistent': persistent,
        }
        ctx['stack'].append(span)
        _write_unlocked(session_id, ctx)
        return span


def end_span(session_id: str, name: str | None = None) -> dict | None:
    """End the active span (or the one matching `name`) and pop it.

    Returns the completed span dict, or None if not found.
    """
    with _locked(session_id):
        ctx = _read_unlocked(session_id)
        if not ctx['stack']:
            return None

        idx = len(ctx['stack']) - 1
        if name is not None:
            for i in range(len(ctx['stack']) - 1, -1, -1):
                if ctx['stack'][i]['name'] == name:
                    idx = i
                    break
            else:
                return None

        span = ctx['stack'].pop(idx)
        # Anything above it in the stack is implicitly closed.
        ctx['stack'] = ctx['stack'][:idx]

        span['end_time'] = datetime.now().isoformat()
        start_dt = datetime.fromisoformat(span['start_time'])
        end_dt = datetime.fromisoformat(span['end_time'])
        span['duration_ms'] = int((end_dt - start_dt).total_seconds() * 1000)

        _write_unlocked(session_id, ctx)
        return span


def current_span(session_id: str) -> dict | None:
    """Return the current active span, or None."""
    with _locked(session_id):
        ctx = _read_unlocked(session_id)
        return ctx['stack'][-1] if ctx['stack'] else None


def pop_all(session_id: str, preserve_persistent: bool = False) -> list:
    """End all active spans for a session. Returns completed spans.

    If preserve_persistent=True, spans marked persistent=True are kept
    on the stack and only spans above the highest persistent span are popped.
    """
    with _locked(session_id):
        ctx = _read_unlocked(session_id)
        completed = []
        now = datetime.now().isoformat()

        if preserve_persistent:
            persistent_idx = -1
            for i in range(len(ctx['stack']) - 1, -1, -1):
                if ctx['stack'][i].get('persistent'):
                    persistent_idx = i
                    break
            if persistent_idx >= 0:
                spans_to_pop = ctx['stack'][persistent_idx + 1:]
                ctx['stack'] = ctx['stack'][:persistent_idx + 1]
            else:
                spans_to_pop = ctx['stack'][:]
                ctx['stack'] = []
        else:
            spans_to_pop = ctx['stack'][:]
            ctx['stack'] = []

        for span in spans_to_pop:
            span['end_time'] = now
            start_dt = datetime.fromisoformat(span['start_time'])
            end_dt = datetime.fromisoformat(span['end_time'])
            span['duration_ms'] = int((end_dt - start_dt).total_seconds() * 1000)
            completed.append(span)

        _write_unlocked(session_id, ctx)
        return completed
