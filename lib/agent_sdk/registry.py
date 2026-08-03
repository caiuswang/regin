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
# Ids claimed by a launch whose runner does not exist yet, → the token that
# claimed each. A runner registers only after `connect()` has spawned its
# child, so without this an id is unowned for the whole of its startup — long
# enough for a second launch reusing that id (a resume) to be admitted, whose
# runner then replaces the first in `_runs` and leaves a live child nothing can
# stop or steer. Deliberately not a placeholder in `_runs`: `_live_runner`
# calls methods on whatever it finds, and a reserved run is genuinely not
# reachable yet.
_reserved: dict[str, object] = {}
# CLI session id → the run it belongs to. A regin-launched agent is traced
# twice: once as this run, and once as the `claude` session the child reports
# through the hooks it loads. An operator on `/live` can be looking at either
# id, so both have to reach the one live channel — an unaliased child id falls
# through to the tmux bridge, which knows only the pane the server was started
# from.
_aliases: dict[str, str] = {}
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


_MISSING = object()
_ANSWER_KINDS = frozenset({'question'})
_DECISION_KINDS = frozenset({'plan', 'tool'})


def reserve_run(trace_id: str, token: object = None) -> bool:
    """Claim `trace_id` for a launch whose runner does not exist yet.

    False when the id is already live or already claimed. The test and the
    claim happen under one `_lock` acquisition, so of two callers racing for
    the same id exactly one is told to proceed.

    `token` identifies this claim. A release names it, so a launch that ends
    late cannot drop a reservation a *later* launch of the same id now holds.
    """
    with _lock:
        if trace_id in _runs or trace_id in _reserved:
            return False
        _reserved[trace_id] = token
    log.write("sdk_run_reserved", trace_id=trace_id)
    return True


def release_run(trace_id: str, token: object = None) -> None:
    """Give a claimed id back. Idempotent, and a no-op against a claim held
    under a different token."""
    with _lock:
        if _reserved.get(trace_id, _MISSING) is not token:
            return
        _reserved.pop(trace_id, None)
        # A resume aliases the session it continues at claim time, before any
        # runner exists; a launch that dies in that window never reaches
        # `unregister_run`, so the alias is dropped here too or it outlives
        # the run and keeps pointing that session at an id nothing holds.
        _drop_aliases(trace_id)
    log.write("sdk_run_released", trace_id=trace_id)


def register_run(trace_id: str, runner) -> None:
    with _lock:
        _runs[trace_id] = runner
        _reserved.pop(trace_id, None)
    log.write("sdk_run_registered", trace_id=trace_id)


def _drop_aliases(trace_id: str) -> None:
    """Forget every CLI session id pointing at `trace_id`. Caller holds
    `_lock`."""
    for alias in [a for a, run in _aliases.items() if run == trace_id]:
        _aliases.pop(alias, None)


def unregister_run(trace_id: str) -> None:
    with _lock:
        _runs.pop(trace_id, None)
        _reserved.pop(trace_id, None)
        for key in [k for k, ask in _asks.items() if ask.trace_id == trace_id]:
            _asks.pop(key, None)
        _drop_aliases(trace_id)
    log.write("sdk_run_unregistered", trace_id=trace_id)


def register_alias(session_id: str, trace_id: str) -> None:
    """Record that `session_id` — the CLI's own name for the child — is this
    run. Idempotent, so the runner may call it for every message it sees."""
    if not session_id or session_id == trace_id:
        return
    with _lock:
        if _aliases.get(session_id) == trace_id:
            return
        _aliases[session_id] = trace_id
    log.write("sdk_session_aliased", trace_id=trace_id, session_id=session_id)


def owning_run(trace_id: str) -> str:
    """The run `trace_id` names — itself, or the run it is a child session of.

    Every entry point normalizes through this, so a caller holding either id
    reaches the same runner.
    """
    with _lock:
        return _aliases.get(trace_id, trace_id)


def is_sdk_owned(trace_id: str) -> bool:
    """True when this process holds a live typed channel for `trace_id`, or is
    starting one: a reserved id belongs to a launch already under way, and a
    caller asking who owns it is deciding whether to launch another."""
    trace_id = owning_run(trace_id)
    with _lock:
        return trace_id in _runs or trace_id in _reserved


def is_starting(trace_id: str) -> bool:
    """True when a launch holds `trace_id` but its channel is not up yet.

    `is_sdk_owned` covers both states because a caller deciding whether to
    launch treats them alike. A caller *refusing* cannot: only a registered
    run can be stopped, so telling an operator to stop a starting one sends
    them to a control that will answer "no live agent session".
    """
    trace_id = owning_run(trace_id)
    with _lock:
        return trace_id not in _runs and trace_id in _reserved


def active_run_count(exclude: str = "") -> int:
    """Live runners plus the ids reserved for runs still starting.

    `exclude` drops one id from the tally: a run checking capacity for itself
    already holds its own reservation, and counting it would deny the last
    slot to the run that legitimately claimed it.
    """
    with _lock:
        return len({*_runs, *_reserved} - {exclude})


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
    trace_id = owning_run(trace_id)
    with _lock:
        return next((ask for ask in _asks.values()
                     if ask.trace_id == trace_id), None)


def pending_asks(trace_id: str) -> list[PendingAsk]:
    """Every call this session is parked on, oldest first."""
    trace_id = owning_run(trace_id)
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
    trace_id = owning_run(trace_id)
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


def queued_prompts(trace_id: str) -> list[dict]:
    """`{id, text}` for each prompt waiting behind this run's current turn,
    oldest first. The id is what `edit_queued` / `cancel_queued` name.

    Read straight off the runner rather than through `_live_runner`: this is a
    read, and a session whose loop has stopped still holds prompts nothing will
    ever run — reporting them is more honest than an empty queue, and the
    caller is a status poll, not a delivery.
    """
    trace_id = owning_run(trace_id)
    with _lock:
        runner = _runs.get(trace_id)
    if runner is None:
        return []
    return list(getattr(runner, "pending_prompts", list)())


def edit_queued(trace_id: str, prompt_id: str, text: str) -> tuple[bool, str]:
    """Rewrite a prompt still waiting behind this run's current turn.

    Routed through `_live_runner` rather than the plain lookup `queued_prompts`
    uses: a read of a dead session's leftover queue is honest, but *editing* it
    would report success for a change nothing will ever run.
    """
    runner, refusal = _live_runner(trace_id)
    if runner is None:
        return False, refusal
    ok, detail = runner.edit_pending(prompt_id, text)
    if ok:
        log.write("sdk_prompt_edited", trace_id=trace_id, prompt_id=prompt_id)
    return ok, detail


def cancel_queued(trace_id: str, prompt_id: str) -> tuple[bool, str]:
    """Drop a prompt still waiting behind this run's current turn. The turn in
    flight is untouched — cancelling that is `interrupt_run`."""
    runner, refusal = _live_runner(trace_id)
    if runner is None:
        return False, refusal
    ok, detail = runner.cancel_pending(prompt_id)
    if ok:
        log.write("sdk_prompt_cancelled", trace_id=trace_id,
                  prompt_id=prompt_id)
    return ok, detail


def run_phase(trace_id: str) -> str | None:
    """What this run is actually doing, or None when this process owns no run.

    The serve-time phase is otherwise inferred from span timestamps, which for
    a run regin launched is a worse source than the runner sitting in the same
    process: a five-minute tool call emits no spans and reads `inactive-stale`
    while the child is plainly working, and a park is guessed from whether a
    PENDING span happens to be the newest rather than read from `_asks`, which
    knows. None means "not ours" — the caller keeps its heuristic, which is the
    only thing a session regin merely traces has.

    Ordering is by what the operator most needs to see: a park outranks the
    turn holding it open, and a stop already asked for outranks the turn it is
    ending.
    """
    trace_id = owning_run(trace_id)
    with _lock:
        runner = _runs.get(trace_id)
        reserved = trace_id in _reserved
        kinds = {ask.kind for ask in _asks.values() if ask.trace_id == trace_id}
    if runner is None:
        return 'starting' if reserved else None
    if kinds & _ANSWER_KINDS:
        return 'waiting-input'
    if kinds:
        return 'waiting-permission'
    if runner.stop_requested:
        return 'stopping'
    return 'working' if runner.turn_in_flight else 'idle'


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
    trace_id = owning_run(trace_id)
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
