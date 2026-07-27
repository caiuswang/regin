"""Process-local registry of live runners and the questions they're parked on.

When an SDK-owned session calls `AskUserQuestion`, the tool blocks inside the
SDK on an `asyncio.Future` held here. The answer arrives later on a completely
different thread — a Flask request handler — so resolving it has to hop back
onto the runner's event loop via `call_soon_threadsafe`. That thread hop is the
reason this module exists rather than a plain dict on the runner.

Scope is deliberately one process. A runner only lives as long as the process
that spawned it, so a registry entry and a live channel have the same lifetime;
the durable `agent_runs` row is a record of intent, not a substitute for this.

A session can hold **several** parked calls at once: one assistant message
routinely carries several tool calls, and with tool gating on, each of them
parks. `/live` serves them one at a time rather than presenting a queue — each
parked call is already its own row and card in the tail, and the decision card
posts that span's `tool_use_id` so it resolves exactly the call the operator
was looking at. A resolver that names no call takes the oldest of its kind.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
from dataclasses import dataclass

from lib.activity_log import get_activity_logger

log = get_activity_logger("agent_sdk")

_lock = threading.Lock()
_runs: dict[str, object] = {}
# Insertion-ordered so an untargeted resolve takes the oldest match. Keyed by a
# counter rather than by (trace_id, tool_use_id): a permission context can
# arrive without a tool_use_id, and two unnamed parks must still coexist rather
# than overwrite each other into a call nothing will ever resolve.
_asks: dict[int, "PendingAsk"] = {}
_ask_ids = itertools.count(1)


@dataclass
class PendingAsk:
    """A tool call blocked inside the SDK, waiting on the operator.

    `kind` is one of `PERMISSION_KINDS`. A question is resolved with the
    operator's answers; a plan or a gated tool with an allow/deny decision.
    The two payloads are not interchangeable — handing a decision to a tool
    expecting `answers` runs it with none — so the resolvers check it.
    """

    trace_id: str
    tool_use_id: str
    tool_input: dict
    future: asyncio.Future
    loop: asyncio.AbstractEventLoop
    kind: str = 'question'


_ANSWER_KINDS = frozenset({'question'})
_DECISION_KINDS = frozenset({'plan', 'tool'})


def register_run(trace_id: str, runner) -> None:
    with _lock:
        _runs[trace_id] = runner
    log.write("sdk_run_registered", trace_id=trace_id)


def unregister_run(trace_id: str) -> None:
    with _lock:
        _runs.pop(trace_id, None)
        for key in [k for k, ask in _asks.items() if ask.trace_id == trace_id]:
            _asks.pop(key, None)
    log.write("sdk_run_unregistered", trace_id=trace_id)


def is_sdk_owned(trace_id: str) -> bool:
    """True when this process holds a live typed channel for `trace_id`."""
    with _lock:
        return trace_id in _runs


def active_run_count() -> int:
    with _lock:
        return len(_runs)


def register_ask(ask: PendingAsk) -> int:
    """Park `ask` and return its id — the handle for discarding this one call
    without touching the others the session may be holding."""
    key = next(_ask_ids)
    with _lock:
        _asks[key] = ask
    log.write("sdk_ask_parked", trace_id=ask.trace_id,
              tool_use_id=ask.tool_use_id, kind=ask.kind)
    return key


def get_ask(trace_id: str) -> PendingAsk | None:
    """The session's oldest parked call, whatever its kind."""
    with _lock:
        return next((ask for ask in _asks.values()
                     if ask.trace_id == trace_id), None)


def pending_asks(trace_id: str) -> list[PendingAsk]:
    """Every call this session is parked on, oldest first."""
    with _lock:
        return [ask for ask in _asks.values() if ask.trace_id == trace_id]


def discard_ask(ask_id: int) -> None:
    """Drop one park by its id. A trace-wide drop would take calls belonging to
    the same session's other parked tools with it."""
    with _lock:
        _asks.pop(ask_id, None)


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


def resolve_ask(trace_id: str, result, tool_use_id: str = "") -> tuple[bool, str]:
    """Hand the operator's answers to a parked question. Any thread.

    `tool_use_id` names which parked question, for a session holding more than
    one; without it the oldest is answered.
    """
    return _resolve(trace_id, result, _ANSWER_KINDS, "no pending question",
                    "question already answered", "answer delivered",
                    tool_use_id)


def resolve_permission(trace_id: str, decision,
                       tool_use_id: str = "") -> tuple[bool, str]:
    """Hand an allow/deny decision to a parked plan or gated tool call.

    Separate from `resolve_ask` because the payloads are different shapes and
    a park only accepts its own: an answer list means nothing to a gated Bash,
    and a decision means nothing to a question.
    """
    return _resolve(trace_id, decision, _DECISION_KINDS,
                    "no pending permission request",
                    "permission request already decided", "decision delivered",
                    tool_use_id)


def _matches(ask: "PendingAsk", trace_id: str, kinds: frozenset,
             tool_use_id: str) -> bool:
    if ask.trace_id != trace_id or ask.kind not in kinds:
        return False
    return not tool_use_id or ask.tool_use_id == tool_use_id


def _take(trace_id: str, kinds: frozenset,
          tool_use_id: str) -> "tuple[int, PendingAsk] | None":
    """Find the oldest matching park, or None. A mismatched resolver must leave
    every park standing — including the operator's real question.

    The park is *not* popped here: a resolve can still fail on a dead loop, and
    consuming the only handle to the call on the way to refusing would leave it
    unresolvable by anyone. `_resolve` drops it once delivery is certain."""
    with _lock:
        key = next((k for k, ask in _asks.items()
                    if _matches(ask, trace_id, kinds, tool_use_id)), None)
        return (key, _asks[key]) if key is not None else None


def _resolve(trace_id: str, result, kinds: frozenset, missing: str,
             already: str, delivered: str,
             tool_use_id: str = "") -> tuple[bool, str]:
    found = _take(trace_id, kinds, tool_use_id)
    if found is None:
        return False, missing
    ask_id, ask = found
    if ask.future.done():
        return False, already
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
    with _lock:
        _asks.pop(ask_id, None)
    log.write("sdk_ask_resolved", trace_id=trace_id, kind=ask.kind,
              tool_use_id=ask.tool_use_id)
    return True, delivered
