"""Process-local registry of live runners and the questions they're parked on.

When an SDK-owned session calls `AskUserQuestion`, the tool blocks inside the
SDK on an `asyncio.Future` held here. The answer arrives later on a completely
different thread — a Flask request handler — so resolving it has to hop back
onto the runner's event loop via `call_soon_threadsafe`. That thread hop is the
reason this module exists rather than a plain dict on the runner.

Scope is deliberately one process. A runner only lives as long as the process
that spawned it, so a registry entry and a live channel have the same lifetime;
the durable `agent_runs` row is a record of intent, not a substitute for this.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

from lib.activity_log import get_activity_logger

log = get_activity_logger("agent_sdk")

_lock = threading.Lock()
_runs: dict[str, object] = {}
_asks: dict[str, "PendingAsk"] = {}


@dataclass
class PendingAsk:
    """An `AskUserQuestion` blocked inside the SDK, waiting on the operator."""

    trace_id: str
    tool_use_id: str
    tool_input: dict
    future: asyncio.Future
    loop: asyncio.AbstractEventLoop


def register_run(trace_id: str, runner) -> None:
    with _lock:
        _runs[trace_id] = runner
    log.write("sdk_run_registered", trace_id=trace_id)


def unregister_run(trace_id: str) -> None:
    with _lock:
        _runs.pop(trace_id, None)
        _asks.pop(trace_id, None)
    log.write("sdk_run_unregistered", trace_id=trace_id)


def is_sdk_owned(trace_id: str) -> bool:
    """True when this process holds a live typed channel for `trace_id`."""
    with _lock:
        return trace_id in _runs


def active_run_count() -> int:
    with _lock:
        return len(_runs)


def register_ask(ask: PendingAsk) -> None:
    with _lock:
        _asks[ask.trace_id] = ask
    log.write("sdk_ask_parked", trace_id=ask.trace_id,
              tool_use_id=ask.tool_use_id)


def get_ask(trace_id: str) -> PendingAsk | None:
    with _lock:
        return _asks.get(trace_id)


def discard_ask(trace_id: str) -> None:
    with _lock:
        _asks.pop(trace_id, None)


def _live_runner(trace_id: str):
    """(runner, refusal) for a run this process can still reach.

    A registry entry alone isn't reachability: the runner's loop is what
    accepts work, and a stopped loop takes `call_soon_threadsafe` without
    raising and silently never runs the callback — the same trap
    `resolve_ask` guards, which is the difference between refusing and
    telling the operator their prompt was delivered to nothing.
    """
    with _lock:
        runner = _runs.get(trace_id)
    if runner is None:
        return None, "no live agent session"
    loop = getattr(runner, "loop", None)
    if loop is None or not loop.is_running() or loop.is_closed():
        return None, "agent session is no longer running"
    return runner, ""


def submit_prompt(trace_id: str, text: str) -> tuple[bool, str]:
    """Queue a follow-up prompt. Safe to call from any thread."""
    runner, refusal = _live_runner(trace_id)
    if runner is None:
        return False, refusal
    if getattr(runner, "is_stopping", False):
        return False, "agent session is stopping"
    try:
        runner.loop.call_soon_threadsafe(runner.enqueue, text)
    except RuntimeError as exc:
        log.error("sdk_prompt_queue_failed", trace_id=trace_id, detail=str(exc))
        return False, "agent session is no longer running"
    log.write("sdk_prompt_queued", trace_id=trace_id)
    return True, "prompt queued"


def stop_run(trace_id: str) -> tuple[bool, str]:
    """End the session, including one that is mid-turn.

    `request_stop` interrupts the turn in flight as well as closing the queue,
    so this can't return "stopping" for a session that will in fact keep
    running until the server restarts.
    """
    runner, refusal = _live_runner(trace_id)
    if runner is None:
        return False, refusal
    future = asyncio.run_coroutine_threadsafe(runner.request_stop(),
                                              runner.loop)
    future.add_done_callback(lambda f: _log_control(trace_id, "stop", f))
    log.write("sdk_run_stop_requested", trace_id=trace_id)
    return True, "stopping"


def interrupt_run(trace_id: str) -> tuple[bool, str]:
    """Cancel the turn in flight, leaving the session open for the next one."""
    runner, refusal = _live_runner(trace_id)
    if runner is None:
        return False, refusal
    future = asyncio.run_coroutine_threadsafe(runner.interrupt(), runner.loop)
    future.add_done_callback(lambda f: _log_control(trace_id, "interrupt", f))
    log.write("sdk_run_interrupt_requested", trace_id=trace_id)
    return True, "interrupt sent"


def _log_control(trace_id: str, action: str, future) -> None:
    """Drain a control call's result so a refusal from the CLI is logged rather
    than swallowed by the loop's default exception handler."""
    error = future.exception() if not future.cancelled() else None
    if error is not None:
        log.error("sdk_run_control_failed", trace_id=trace_id, action=action,
                  detail=repr(error))


def resolve_ask(trace_id: str, result) -> tuple[bool, str]:
    """Hand `result` to the parked tool call. Safe to call from any thread."""
    with _lock:
        ask = _asks.pop(trace_id, None)
    if ask is None:
        return False, "no pending question"
    if ask.future.done():
        return False, "question already answered"
    # A *stopped* loop accepts call_soon_threadsafe without raising and simply
    # never runs the callback, so checking liveness first is the difference
    # between a refusal and telling the operator their answer was delivered
    # when nothing received it. Only a closed loop raises.
    if not ask.loop.is_running() or ask.loop.is_closed():
        log.error("sdk_ask_resolve_failed", trace_id=trace_id,
                  detail="runner loop not running")
        return False, "agent session is no longer running"
    try:
        ask.loop.call_soon_threadsafe(ask.future.set_result, result)
    except RuntimeError as exc:
        log.error("sdk_ask_resolve_failed", trace_id=trace_id, detail=str(exc))
        return False, "agent session is no longer running"
    log.write("sdk_ask_resolved", trace_id=trace_id,
              tool_use_id=ask.tool_use_id)
    return True, "answer delivered"
