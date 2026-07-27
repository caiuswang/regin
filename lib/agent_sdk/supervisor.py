"""Run agent sessions inside the web process.

The parked-question registry is process-local, so answering from `/live` only
works if the runner lives in the same process as the Flask route that resolves
it. This module owns one long-lived asyncio loop on a daemon thread and
schedules runs onto it — that shared loop is what makes the typed answer path
reachable from a browser at all.

The loop is created lazily: a regin install with `agent_sdk` off never starts a
thread, and importing this module costs nothing.
"""

from __future__ import annotations

import asyncio
import threading
import uuid

from lib.activity_log import get_activity_logger
from lib.settings import settings
from . import registry
from .runner import run_once

log = get_activity_logger("agent_sdk")

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None


class LaunchRefused(RuntimeError):
    """Structured refusal — disabled, at capacity, or missing the SDK."""


def _serve(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _lock:
        if _loop is not None and not _loop.is_closed():
            return _loop
        _loop = asyncio.new_event_loop()
        threading.Thread(target=_serve, args=(_loop,), daemon=True,
                         name="agent-sdk-loop").start()
        return _loop


def _on_done(trace_id: str, future) -> None:
    """Drain the run's result so a failure is logged rather than swallowed by
    the loop's default exception handler."""
    error = future.exception() if not future.cancelled() else None
    if error is not None:
        log.error("sdk_run_crashed", trace_id=trace_id, detail=repr(error))


def launch(prompt: str, *, cwd: str | None = None) -> str:
    """Start a session for `prompt` and return its trace id immediately.

    Returns as soon as the run is scheduled — the agent works on the shared
    loop while the caller's request completes, which is what lets `/live` show
    the session and answer its questions while it runs.
    """
    if not settings.agent_sdk.enabled:
        raise LaunchRefused("agent_sdk disabled")
    if not (prompt or "").strip():
        raise LaunchRefused("prompt required")
    if registry.active_run_count() >= settings.agent_sdk.max_concurrent_runs:
        raise LaunchRefused("max_concurrent_runs reached")
    trace_id = f"sdk-{uuid.uuid4().hex[:12]}"
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(
        run_once(trace_id, prompt, cwd=cwd), loop)
    future.add_done_callback(lambda f: _on_done(trace_id, f))
    log.write("sdk_run_launched", trace_id=trace_id, cwd=cwd)
    return trace_id
