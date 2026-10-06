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
import functools
import itertools
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from lib.activity_log import get_activity_logger

if TYPE_CHECKING:
    from lib.agent_bridge.screen_state import ScreenState

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
# Run id → prompts submitted while it held only a reservation, in send order.
# The window is not hypothetical and the composer is offered inside it:
# `is_sdk_owned` counts a reservation, so the trace view swaps its Resume bar
# for a composer the moment a launch is scheduled, while `_live_runner` reads
# `_runs` alone and would refuse every send until `connect()` returns. Only a
# resume can reach it — a fresh launch carries its prompt in the launch call.
_reserved_prompts: dict[str, list["_Parked"]] = {}
# CLI session id → the run it belongs to. A regin-launched agent is traced
# twice: once as this run, and once as the `claude` session the child reports
# through the hooks it loads. An operator on `/live` can be looking at either
# id, so both have to reach the one live channel — an unaliased child id falls
# through to the tmux bridge, which knows only the pane the server was started
# from.
_aliases: dict[str, str] = {}
# Run id → the child session it is driving RIGHT NOW. `_aliases` is many-to-one
# and never shrinks, because `/clear` retires a child rather than replacing it:
# the CLI ends that session and starts a fresh one under a new id, so a run
# accumulates children and every past id must keep resolving. This holds the
# other direction — which of them is still live — so a reader looking at a
# retired child (or at the run itself, whose durable `cli_session_id` names the
# first child forever) can point at the conversation that is actually running.
_live_children: dict[str, str] = {}
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
    tool_name: str = ''
    # Stamped at park time so the blocker feed can say how long the operator
    # has been waited on; ISO local, same domain as span start_time.
    parked_at: str = ''


class _Parked(NamedTuple):
    """A prompt accepted for a run whose runner does not exist yet.

    Held here rather than on the runner because the runner is exactly what the
    reservation window lacks. `row_id` names the `bridge_messages` row the
    composer wrote before submitting — the durable copy — so a launch that
    dies in the window marks that row dropped instead of losing the text.
    """

    text: str
    images: list
    row_id: int | None


# What the composer is told when a send lands in that window. Distinct from
# plain "prompt queued" because the two are different promises: this one says
# the session is not up yet, which is the honest answer to a card that has
# been showing a composer for a couple of seconds longer than it should.
_PARKED_DETAIL = "prompt queued — session is still starting"

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
        parked = _reserved_prompts.pop(trace_id, [])
        # A resume aliases the session it continues at claim time, before any
        # runner exists; a launch that dies in that window never reaches
        # `unregister_run`, so the alias is dropped here too or it outlives
        # the run and keeps pointing that session at an id nothing holds.
        _drop_aliases(trace_id)
    log.write("sdk_run_released", trace_id=trace_id)
    _abandon_parked(trace_id, parked, "launch_released")


def register_run(trace_id: str, runner) -> None:
    with _lock:
        _runs[trace_id] = runner
        _reserved.pop(trace_id, None)
        # A run coming alive re-opens the alias question for every child it
        # recorded in a previous life — the resumed run's earlier children are
        # the whole population this fix is for.
        _durable_misses.clear()
        parked = _reserved_prompts.pop(trace_id, [])
    log.write("sdk_run_registered", trace_id=trace_id)
    # Drained outside the lock, and only after `_runs` holds the runner: a send
    # racing this finds the reservation already gone and takes the ordinary
    # live path, instead of appending to a list nothing will drain again.
    for item in parked:
        _release_parked(trace_id, runner, item)


def _drop_aliases(trace_id: str) -> None:
    """Forget every CLI session id pointing at `trace_id`. Caller holds
    `_lock`."""
    for alias in [a for a, run in _aliases.items() if run == trace_id]:
        _aliases.pop(alias, None)
    _live_children.pop(trace_id, None)


def unregister_run(trace_id: str) -> None:
    with _lock:
        _runs.pop(trace_id, None)
        _reserved.pop(trace_id, None)
        parked = _reserved_prompts.pop(trace_id, [])
        for key in [k for k, ask in _asks.items() if ask.trace_id == trace_id]:
            _asks.pop(key, None)
        _drop_aliases(trace_id)
    log.write("sdk_run_unregistered", trace_id=trace_id)
    # Normally empty — a run that got here went through `register_run` (which
    # drained) or `release_run`. Reported rather than discarded for the same
    # reason the pop above is defensive: a teardown that skipped both would
    # otherwise lose the text with no artifact anywhere.
    _abandon_parked(trace_id, parked, "run_torn_down_while_starting")


def _abandon_parked(trace_id: str, parked: list[_Parked], reason: str) -> None:
    """Report every parked prompt that will now never run.

    The composer cleared the operator's draft on this prompt's ack, so the
    `bridge_messages` row is the last copy of the text — the same contract
    `AgentRunner._report_drop` keeps for a prompt abandoned after the runner
    took it.
    """
    for item in parked:
        log.error("sdk_parked_prompt_dropped", trace_id=trace_id,
                  row_id=item.row_id, reason=reason, chars=len(item.text),
                  text_head=item.text[:120])
        _mark_parked_dropped(item, reason)


def _mark_parked_dropped(item: _Parked, reason: str) -> None:
    """Stop a parked prompt's row reading `delivered` when nothing will run
    it. A failure to record must not propagate — the caller is a teardown."""
    if item.row_id is None:
        return
    try:
        from lib.agent_bridge import store as bridge_store

        bridge_store.mark_dropped(item.row_id, reason)
    except Exception as exc:  # noqa: BLE001 — teardown must not raise
        log.error("sdk_parked_drop_unrecorded", row_id=item.row_id,
                  detail=str(exc))


def _release_parked(trace_id: str, runner, item: _Parked) -> None:
    """Hand one parked prompt to the runner that has just come up.

    Routed through the same `call_soon_threadsafe` hop `submit_prompt` uses
    rather than calling `enqueue` directly: `register_run` runs on the
    runner's loop in production but not in every caller, and the hop is
    correct from both. Ordering survives it — a launch prompt is enqueued
    before `start()`, and callbacks run in submission order, so a parked
    prompt lands behind it exactly as the operator sent it.
    """
    loop = getattr(runner, "loop", None)
    try:
        # `is_running` as well as `is_closed`: a stopped loop takes
        # `call_soon_threadsafe` without raising and never runs the callback,
        # which would swallow a prompt whose ack already cleared the draft —
        # the trap `_live_runner` guards for the same reason.
        if loop is None or loop.is_closed() or not loop.is_running():
            raise RuntimeError("runner has no usable loop")
        loop.call_soon_threadsafe(
            functools.partial(runner.enqueue, item.text, images=item.images,
                              row_id=item.row_id))
    except RuntimeError as exc:
        log.error("sdk_parked_prompt_lost", trace_id=trace_id,
                  row_id=item.row_id, detail=str(exc))
        _mark_parked_dropped(item, "runner_unreachable_at_register")
        return
    log.write("sdk_parked_prompt_released", trace_id=trace_id,
              row_id=item.row_id, chars=len(item.text))


def _park_if_starting(trace_id: str, item: _Parked) -> bool:
    """Hold `item` for a run that is reserved but has no runner yet.

    One lock acquisition, and tried BEFORE `_live_runner`: the two states are
    adjacent in time — a launch moves from reserved to running between them —
    so a park that loses the race finds the reservation gone and falls through
    to the live path, while one that wins is drained by the `register_run`
    that takes the same lock. Neither order loses the prompt.
    """
    with _lock:
        if trace_id in _runs or trace_id not in _reserved:
            return False
        _reserved_prompts.setdefault(trace_id, []).append(item)
    return True


def _announce_successor(retired: str, run_trace_id: str,
                        successor: str) -> None:
    """Publish the `/clear` succession this run just made.

    `agent_bridge.succession` is the OTHER tier's publisher and resolves the
    heir through the pane registry, which a regin-launched child never enters
    (`agent_sdk/client.py` sets `REGIN_BRIDGE=0` for it on purpose). It
    therefore stays silent for a run, and a page holding the retired id was
    never moved off a session that had ended. This is the site that learns the
    fact — the run's own reader, naming the child the CLI just opened — so it
    is the one that can raise the frame.

    Both ids that strand are named: the child that was retired, and the run
    itself, whose durable `cli_session_id` keeps naming the first child
    forever. A page can be mounted on either, and the client acts only on the
    frame whose `trace_id` matches the route it is on, so raising both costs
    one ignored frame and covers both arrivals.

    Called after `_lock` is released and the pointer is committed: a frame that
    raced its own write would send readers back to re-read the old child.
    Best-effort — this runs on the runner's message path, and a fan-out failure
    must not break the turn that reported the new session.
    """
    try:
        from lib.notifications import hub
        for stranded in (retired, run_trace_id):
            hub.broadcast_event(hub.SUCCESSOR_EVENT, {
                'trace_id': stranded,
                'successor_trace_id': successor,
            })
    except Exception:  # noqa: BLE001 - an announcement must never fail a turn
        log.error("sdk_successor_announce_failed", trace_id=retired,
                  exc_info=True)
        return
    log.write("sdk_successor_announced", trace_id=retired,
              successor=successor)


def register_alias(session_id: str, trace_id: str) -> None:
    """Record that `session_id` — the CLI's own name for the child — is this
    run. Idempotent, so the runner may call it for every message it sees."""
    if not session_id or session_id == trace_id:
        return
    with _lock:
        # Only a child seen for the FIRST time becomes the live one. Re-stating
        # an alias that already holds must not move the pointer backwards: the
        # supervisor registers a resumed session's id at claim time, and the
        # runner may by then have moved on to a child `/clear` opened.
        if _aliases.get(session_id) == trace_id:
            return
        _aliases[session_id] = trace_id
        _durable_misses.clear()
        retired = _live_children.get(trace_id)
        _live_children[trace_id] = session_id
    log.write("sdk_session_aliased", trace_id=trace_id, session_id=session_id)
    # A previous child means this one replaced it, which only `/clear` does.
    # The first child of a run succeeds nothing — that is the initial link.
    if retired and retired != session_id:
        _announce_successor(retired, trace_id, session_id)


def live_child(trace_id: str) -> str | None:
    """The child session a live run is driving now, when that is NOT the
    session `trace_id` names.

    `/clear` moves it, and so does a resume the CLI renames: either way the id
    an operator is holding — the run's own `sdk-…`, or the child that was just
    retired — names a session that is over while the agent keeps working under
    a new id. Both of those resolve here to the live one; the live child itself
    resolves to None, since there is nothing to redirect.

    Resolves through `owning_run`, so a child recorded only in the DB is
    answered too: that is the whole population this matters for, since a child
    the registry aliased in this process is usually the live one already.

    Liveness itself stays process-local — `_runs` is the gate — because a run
    does not outlive the process holding it.
    """
    run = owning_run(trace_id)
    with _lock:
        if run not in _runs:
            return None
        child = _live_children.get(run)
    return child if child and child != trace_id else None


def owning_run(trace_id: str) -> str:
    """The run `trace_id` names — itself, or the run it is a child session of.

    Every entry point normalizes through this, so a caller holding either id
    reaches the same runner.

    `_aliases` alone answers only for children THIS process watched arrive, and
    a run outlives that: restarting the server, then resuming the run, leaves
    every earlier child of it unaliased. Those ids are not unknown — the DB
    recorded them (`agent_run_sessions`) — and the resume path reads them from
    there, so a registry that refuses to is the second resolver in a pair that
    then disagree: `resumable` sees a live run and refuses ("run_live"), this
    sees no run and reports nothing steerable, and the page renders neither a
    Resume button nor a composer. The DB link is the durable half; consult it
    when the in-process one misses.

    Only when a run is actually held — with nothing live or starting, every id
    resolves to itself regardless, and the lookup would be a query per call on
    the hot message path for an answer that cannot change the outcome. A
    reservation counts: the window it covers is a resume's own startup, which
    is exactly when a page sits on a child of the run being relaunched.
    """
    with _lock:
        run = _aliases.get(trace_id)
        if run is not None:
            return run
        if trace_id in _runs or trace_id in _reserved:
            return trace_id
        if not _runs and not _reserved:
            return trace_id
    return _durable_alias(trace_id) or trace_id


# Child session id → the run it belongs to, as recorded in the DB, and the ids
# known NOT to be children. A child's run never changes once written, so a hit
# holds for the life of the process. A miss is cached too — without it every
# hook-only session pays a query on every poll for as long as any run is held —
# and invalidated at the two points that can turn one into a hit: a child is
# only ever written to the DB alongside `register_alias` (see
# `runner._note_session`), and a run becoming live re-opens the question for
# every child it recorded in a previous life.
_durable: dict[str, str] = {}
_durable_misses: set[str] = set()


def _forget_durable_misses() -> None:
    with _lock:
        _durable_misses.clear()


def _durable_alias(trace_id: str) -> str | None:
    """`agent_run_sessions`' answer to `owning_run`, or None.

    Best-effort: this sits under every steer and every liveness read, and an
    unmigrated or locked DB must degrade to the process-local answer rather
    than raise out of a turn.
    """
    cached = _durable.get(trace_id)
    if cached is not None:
        return cached
    if trace_id in _durable_misses:
        return None
    try:
        from lib.agent_sdk import store
        run = store.sdk_trace_for_cli_session(trace_id)
    except Exception:  # noqa: BLE001 - a steer must never fail on this lookup
        log.error("sdk_durable_alias_failed", trace_id=trace_id, exc_info=True)
        return None
    with _lock:
        if run:
            _durable[trace_id] = run
        else:
            _durable_misses.add(trace_id)
    return run


def is_driving(trace_id: str) -> bool:
    """Whether the run holding `trace_id` is on THIS conversation right now.

    The sharper half of `is_sdk_owned`, and the one a WRITE has to ask.
    Ownership resolves through the run, so every child a run ever named
    reports owned — including ones it finished with, whose `ended_at` is a
    true verdict. A caller about to clear that marker needs to know the run is
    on this session and not merely related to it.

    The run's own id qualifies: it names the run rather than a conversation,
    so whatever the run is doing, it is doing under that trace. A child
    qualifies only while it is the recorded live one — which is deliberately
    False in the window before a new child reports itself, since nothing then
    proves the run moved to the ended session being asked about.
    """
    run = owning_run(trace_id)
    with _lock:
        if run not in _runs and run not in _reserved:
            return False
        return run == trace_id or _live_children.get(run) == trace_id


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


def live_session_ids() -> set[str]:
    """Every session id this process holds a RUNNING runner for — run ids and
    the child session ids aliased onto them.

    For callers that must decide liveness for MANY sessions at once (the
    session list's `active` facet) and cannot afford a `run_phase` call per
    row.

    Deliberately NOT `_reserved`, which `is_sdk_owned` does include. The two
    answer different questions: that one is "would launching another collide",
    where a launch under way counts; this one is "is a runner holding this
    session", where it does not — a reserved id has no runner, `run_phase`
    reports it `starting`, and the client does not call `starting` active
    either. Including it put a resuming session inside `active_count` while
    its own chip read "starting".

    An alias is included only while the run it points at is still live:
    `_aliases` is pruned on teardown alone, so a child `/clear` retired still
    resolves through `owning_run`, and reporting it as live is the bug that
    guard exists to avoid.
    """
    with _lock:
        return set(_runs) | {sid for sid, run in _aliases.items() if run in _runs}


def owned_run_phases() -> dict[str, str]:
    """`run_phase` for every session id it would answer non-None for.

    The session list classifies thousands of rows in SQL and cannot call
    `run_phase` per row, so it takes this map instead. It must name exactly the
    ids `run_phase` answers for — live runs, reservations, their in-process
    aliases, and the children only `agent_run_sessions` remembers (a run
    resumed after a restart) — or a row's chip and the facet that filters it
    disagree.
    """
    with _lock:
        held = set(_runs) | set(_reserved)
        if not held:
            return {}
        ids = held | {sid for sid, run in _aliases.items() if run in held}
    try:
        from lib.agent_sdk import store
        ids |= store.child_session_ids_of(held)
    except Exception:  # noqa: BLE001 - a list read must never fail on this lookup
        log.error("sdk_owned_children_failed", exc_info=True)
    phases = {sid: run_phase(sid) for sid in ids}
    return {sid: phase for sid, phase in phases.items() if phase}


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


def all_pending_asks() -> list[PendingAsk]:
    """Every parked call across every run this process owns, oldest first —
    the blocker feed's authority for sessions regin launched itself."""
    with _lock:
        return list(_asks.values())


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


def submit_prompt(trace_id: str, text: str, images: list | None = None,
                  row_id: int | None = None) -> tuple[bool, str]:
    """Queue a follow-up prompt. Safe to call from any thread.

    `images` are `{media_type, data_b64}` entries the composer staged; they
    travel with the prompt so the CLI is handed the picture itself rather than
    a path to go and open.

    `row_id` is the `bridge_messages` row the composer recorded. It rides the
    queue entry so the runner can correct that row if the prompt is abandoned
    before it runs — this ack is not proof it will, only that the loop took
    the call (see `_live_runner`), and everything after this point is the
    runner's to report.

    A run that is still starting parks the prompt instead of refusing it: its
    id already reads steerable to every caller (`is_sdk_owned` counts a
    reservation), so the composer is on screen for the whole of a launch's
    startup and "no live agent session" was the wrong answer to a send made
    there. `register_run` drains what was parked as the run's next turn.
    """
    run_id = owning_run(trace_id)
    if _park_if_starting(run_id, _Parked(text, list(images or []), row_id)):
        log.write("sdk_prompt_parked", trace_id=run_id, row_id=row_id,
                  chars=len(text))
        return True, _PARKED_DETAIL
    runner, refusal = _live_runner(trace_id)
    if runner is None:
        return False, refusal
    if getattr(runner, "is_stopping", False):
        return False, "agent session is stopping"
    try:
        runner.loop.call_soon_threadsafe(
            functools.partial(runner.enqueue, text, images=images,
                              row_id=row_id))
    except RuntimeError as exc:
        log.error("sdk_prompt_queue_failed", trace_id=trace_id, detail=str(exc))
        return False, "agent session is no longer running"
    log.write("sdk_prompt_queued", trace_id=trace_id, row_id=row_id)
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
    turn holding it open, a stop already asked for outranks the turn it is
    ending, and a retry outranks the `working` it would otherwise be reported
    as — the turn IS in flight, but nothing is moving and the reason is the
    network, which is the one thing silence alone can never say.

    `idle` needs both no turn open AND no delegated background agent still
    running: a background subagent outlives the turn that launched it, and
    the session is busy until it reports back.
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
    return _runner_phase(runner)


def _runner_phase(runner) -> str:
    """`run_phase` for a live runner with nothing parked on it."""
    if runner.stop_requested:
        return 'stopping'
    if runner.retrying is not None:
        return 'retrying'
    if runner.turn_in_flight or runner.delegated_work_in_flight:
        return 'working'
    return 'idle'


class RunLiveState(NamedTuple):
    """Whether a session belongs to this tier, and what its runner says.

    Three answers rather than two, because the caller uses this to pick a
    status source once and then stay with it. `sdk=False` is "not a run regin
    launched", and that caller reads the terminal screen. `sdk=True` with
    `state=None` is "a run, but this process holds no live runner for it" — no
    verdict, and the caller must still not reach for tmux, because a run's
    child is spawned with the bridge off and provably has no pane. Collapsing
    those two into a bare None is what would turn one decision back into a
    probe-and-fall-back chain.
    """

    sdk: bool
    state: "ScreenState | None" = None


def run_live_state(trace_id: str) -> RunLiveState:
    """Live status for the run `trace_id` names, and whether it names one.

    The tier is the durable `agent_runs` row, not this registry: a run whose
    runner died with the server that launched it is still a run, and reading
    it as a terminal session would send a child that never had a pane to tmux.

    Reachability is `_live_runner`'s: a registered runner whose loop has gone
    cannot be working, and reporting the turn it was holding would leave a
    busy dot lit on a session nothing can move.
    """
    runner, _refusal = _live_runner(trace_id)
    if runner is not None:
        return RunLiveState(True, runner.live_state())
    if is_sdk_owned(trace_id):
        return RunLiveState(True)
    from . import store
    return RunLiveState(store.find_run(owning_run(trace_id)) is not None)


def run_live_states() -> dict:
    """`{session_id: ScreenState}` for every run this process owns.

    Keyed by the child session the CLI reports, which is how the terminal
    sweep keys its own output and the id every list reader already holds. A
    run whose child has not named itself yet is keyed by the run id, the only
    one that exists then.
    """
    with _lock:
        owned = list(_runs)
        children = dict(_live_children)
    states = {}
    for trace_id in owned:
        state = run_live_state(trace_id).state
        if state is not None:
            states[children.get(trace_id) or trace_id] = state
    return states


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
