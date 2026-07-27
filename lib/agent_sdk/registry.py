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
