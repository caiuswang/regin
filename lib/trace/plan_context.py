"""Shared plan context for tracking plan mode lifecycle across hook invocations.

Stores per-session plan state in a JSON file so independent hook processes
can coordinate plan session tracking.
"""

import json
import os
import fcntl
import uuid

from lib.providers import get_active_provider
from lib.activity_log import get_activity_logger as _get_activity_logger


def _trace_log():
    return _get_activity_logger("trace_ingest")

PLAN_STATE_DIR = str(get_active_provider().traces_dir())


def _path(session_id: str) -> str:
    os.makedirs(PLAN_STATE_DIR, exist_ok=True)
    return os.path.join(PLAN_STATE_DIR, f"{session_id}_plan.json")


def _read(session_id: str) -> dict | None:
    path = _path(session_id)
    try:
        with open(path, 'r') as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write(session_id: str, state: dict | None) -> None:
    path = _path(session_id)
    if state is None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            json.dump(state, f)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    os.replace(tmp, path)


def enter_plan(session_id: str, plan_filename: str, session_parent_id: str | None = None) -> dict:
    """Record that a plan session has started. Returns state dict with span_ids."""
    from datetime import datetime
    now = datetime.now().isoformat()
    state = {
        'plan_filename': plan_filename,
        'draft_completed': False,
        'session_span_id': uuid.uuid4().hex[:16],
        'draft_span_id': uuid.uuid4().hex[:16],
        'review_span_id': None,
        'session_start_time': now,
        'draft_start_time': now,
        'review_start_time': None,
        'session_parent_id': session_parent_id,
    }
    _write(session_id, state)
    _trace_log().write(
        "plan_session_entered",
        session_id=session_id, plan_filename=plan_filename,
        session_span_id=state['session_span_id'],
    )
    return state


def get_plan_state(session_id: str) -> dict | None:
    """Return the current plan state, or None if not in a plan session."""
    return _read(session_id)


def update_span_ids(session_id: str, session_span_id: str | None = None, draft_span_id: str | None = None) -> dict | None:
    """Update stored span_ids after starting spans on the trace stack."""
    state = _read(session_id)
    if not state:
        return None
    if session_span_id is not None:
        state['session_span_id'] = session_span_id
    if draft_span_id is not None:
        state['draft_span_id'] = draft_span_id
    _write(session_id, state)
    return state


def mark_draft_complete(session_id: str) -> dict | None:
    """Mark the draft phase as complete and allocate a review span_id."""
    from datetime import datetime
    state = _read(session_id)
    if not state:
        return None
    state['draft_completed'] = True
    state['review_span_id'] = uuid.uuid4().hex[:16]
    state['review_start_time'] = datetime.now().isoformat()
    _write(session_id, state)
    _trace_log().write(
        "plan_draft_completed",
        session_id=session_id, review_span_id=state['review_span_id'],
    )
    return state


def exit_plan(session_id: str) -> dict | None:
    """Clear plan state and return the final state dict (with span_ids)."""
    state = _read(session_id)
    _write(session_id, None)
    if state is not None:
        _trace_log().write(
            "plan_session_exited",
            session_id=session_id,
            plan_filename=state.get('plan_filename'),
            session_span_id=state.get('session_span_id'),
        )
    return state
