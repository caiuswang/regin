"""Process-global registry + cancellation flags for in-flight proposal runs.

An external-agent proposal run spawns a subprocess inside a daemon thread
(see `external_jobs._external_proposal_job`). A *different* HTTP request —
the Stop button — needs to interrupt that subprocess, but the `Popen`
handle lives on the worker thread's call stack and the only persisted
handle is the PID. Signalling by PID risks hitting a reused PID once the
process has exited.

This module keeps the live `Popen` handles reachable across threads in the
same process, so Stop can call `proc.terminate()` directly. It also tracks
which run ids the user asked to cancel, so the worker thread can tell a
user-initiated kill (→ `cancelled`) apart from a crash (→ `failed`).

Single-process assumption: regin serves from one threaded process
(`regin serve`), so a module-level dict is shared across the worker thread
and the request thread. A multi-worker WSGI deployment would not share it —
acceptable for the local dashboard this targets.
"""

from __future__ import annotations

import subprocess
import threading


_lock = threading.Lock()
_active: dict[str, subprocess.Popen] = {}
_cancelled: set[str] = set()


def reset(proposal_id: str) -> None:
    """Clear any prior cancel flag / stale handle for `proposal_id`.

    Called at the start of every run so a regenerate that reuses a
    previously-cancelled proposal id isn't insta-cancelled by the stale
    flag.
    """
    with _lock:
        _cancelled.discard(proposal_id)
        _active.pop(proposal_id, None)


def register(proposal_id: str, proc: subprocess.Popen) -> None:
    """Record the live subprocess so a Stop request can terminate it."""
    with _lock:
        _active[proposal_id] = proc


def release(proposal_id: str) -> None:
    """Drop the subprocess handle once the run is done.

    Pops only the handle — the cancel flag is cleared by `reset` at the
    next run start, so the worker's terminal-state logic can still observe
    it after the handle is gone.
    """
    with _lock:
        _active.pop(proposal_id, None)


def is_cancelled(proposal_id: str) -> bool:
    with _lock:
        return proposal_id in _cancelled


def request_cancel(proposal_id: str) -> bool:
    """Mark a run for cancellation and SIGTERM its subprocess if still live.

    Returns True iff a running subprocess was actually signalled. A False
    return is normal when the run is still queued (no process yet) or has
    already exited — the flag is set regardless, so the worker thread will
    finalise the run as `cancelled` when it next checks.
    """
    with _lock:
        _cancelled.add(proposal_id)
        proc = _active.get(proposal_id)
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001 — already-dead / OS race is fine
        return False
    return True


__all__ = [
    "reset",
    "register",
    "release",
    "is_cancelled",
    "request_cancel",
]
