"""Own one regin-launched Claude session end to end.

The runner holds the SDK client, translates every message through the neutral
event union into spans, and parks interactive tool calls until the operator
answers them from `/live`.

A session is **long-lived**: `start()` connects once and a pump coroutine then
drains an `asyncio.Queue` of prompts, one turn at a time, until the queue is
closed or the session goes idle. That queue is the Python counterpart of
paseo's `createAsyncMessageInput` — the difference between a batch job that
ends with its first prompt and an agent you can keep talking to. It is also
what lets a follow-up arrive from a Flask thread mid-run: the sender hops onto
this loop and pushes, and the pump picks it up when the current turn ends.

The other half of that shape is one **session-long reader**: every frame the
CLI sends is drained by `_read_messages`, never by the turn that asked for it.
`receive_response()` stops at the first `ResultMessage`, but a background
`Agent` subagent keeps emitting long after its parent turn has ended, and the
SDK holds those frames in a 100-slot stream shared with the `control_request`
frames that carry permission asks. A turn that abandons the iterator therefore
wedges the whole child at the buffer's high-water mark: nothing answers
`can_use_tool`, the subagent hangs mid-tool-call, and it only moves again when
a new prompt restarts the drain. So the reader owns the stream, and a turn ends
when the reader routes a result to its slot.

**One turn is open at a time.** The agent also speaks with no prompt behind it
— a finished background task wakes it for a follow-up turn of its own — so a
turn is opened lazily for whatever the CLI says while nothing is running. But a
prompt that arrives mid-flight is folded into what the CLI is already saying and
the pair is answered with a single `ResultMessage` carrying no turn label, so a
second turn opened alongside can only end up waiting on a result nobody owes.
The floor is therefore handed over rather than shared: a prompt going out
retires whatever turn was open, and a result with nothing open is dropped
instead of ending a turn it never belonged to.

Spans are posted over HTTP by `lib.hook_plugin.post_span`, which is blocking, so
every write is pushed to a worker thread — a blocked event loop would stall the
`can_use_tool` callback the answer path depends on.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, replace

from lib.activity_log import get_activity_logger
from lib.agent_events import (
    AssistantText,
    AssistantThinking,
    PermissionRequested,
    PermissionResolved,
    SessionEnded,
    SessionStarted,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    UsageUpdated,
    to_span,
    turn_usage_row,
)
from lib.agent_events.usage import context_tokens, turn_uuid
from lib.agent_events.ask import ask_questions
from lib.agent_events.from_sdk import from_sdk_message, prompt_event
from lib.settings import settings
from . import client, policy, registry, store
from .answers import build_updated_input

log = get_activity_logger("agent_sdk")

# Pushed into the prompt queue to end the pump. A prompt is always a non-empty
# string, so None can never collide with real input.
_CLOSE = None


class RunnerBusy(RuntimeError):
    """`max_concurrent_runs` reached."""


@dataclass
class _Turn:
    """One turn in flight, and the identity every row it produces carries.

    `context` lives here rather than on the session because the last API call
    the previous turn made is not this turn's context size, and a usage row is
    stamped with the turn it belongs to.

    `done` is the pump's future. Only a prompted turn has one: it is the only
    turn anybody is waiting on.
    """

    index: int
    done: asyncio.Future | None = None
    context: int | None = None


# `claude_agent_sdk._internal.query.DEFERRING_TASK_TYPES`. Copied rather than
# imported: it is private to the SDK, and this module must stay importable
# where the SDK is not installed.
_TRACKED_TASK_TYPES = frozenset({"local_agent", "local_workflow"})
# `claude_agent_sdk.types.TERMINAL_TASK_STATUSES` — every status meaning the
# task has finished. A killed task is finished too: leaving it tracked would
# pin the teardown gate open for the rest of the session.
_TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "stopped",
                                     "killed"})


class _TaskLedger:
    """Which delegated background tasks are still running.

    A delegated agent task's completion wakes the parent for a follow-up turn
    that ends in its own top-level result; the SDK's own stdin close depends on
    that extra result existing (`query.wait_for_result_and_end_input`). Which
    turn any given result ends is not this ledger's business — nothing on a
    `ResultMessage` says, and the runner keeps one turn open at a time so it
    never has to guess. What is read from here is whether the agent still has
    work that can speak, which is what the teardown gate needs.

    Only delegated agent work counts. A background *shell* —
    `Bash(run_in_background=True)`, `tail -f` — travels the same frames but may
    never reach a terminal status, so tracking one would pin that gate open for
    the life of the session. Terminal state arrives as a `task_notification`
    *or* a `task_updated` patch and not reliably both, so either can retire a
    task — on its status, never on which frame carried it, since `task_updated`
    also reports a task merely starting or pausing.
    """

    def __init__(self) -> None:
        self._tracked: set[str] = set()

    def observe(self, message) -> None:
        """Track one lifecycle frame."""
        task_id = getattr(message, 'task_id', '') or ''
        subtype = getattr(message, 'subtype', '') or ''
        if not task_id:
            return
        if subtype == 'task_started':
            self._track(task_id, message)
        elif subtype in ('task_notification', 'task_updated'):
            self._settle(task_id, message)

    def _track(self, task_id: str, message) -> None:
        data = getattr(message, 'data', None) or {}
        if data.get('task_type') in _TRACKED_TASK_TYPES:
            self._tracked.add(task_id)

    def _settle(self, task_id: str, message) -> None:
        """Retire a task that has reached a terminal status, and only then.

        `task_updated` reports every state change, not just the last one, so
        the status decides this and not the frame it arrived on — clearing a
        `running` patch would send the teardown gate back to trusting a lull
        while the agent still has work that can speak. A task never tracked —
        a background shell — is not retired by reaching one either, since it
        was never held against the gate to begin with.
        """
        if self._status(message) not in _TERMINAL_TASK_STATUSES:
            return
        self._tracked.discard(task_id)

    @staticmethod
    def _status(message) -> str:
        """A task's status, from whichever frame carried it."""
        patch = getattr(message, 'patch', None)
        if isinstance(patch, dict) and patch.get('status'):
            return str(patch['status'])
        return str(getattr(message, 'status', '') or '')

    @property
    def in_flight(self) -> bool:
        """Whether delegated work the agent could still speak about is running.

        A task that has not reported terminal yet is one the agent may still be
        woken by, and the gap between that wake and its first frame is measured
        in seconds — far longer than any lull a teardown should read as "the
        child has finished talking".
        """
        return bool(self._tracked)

    def forget(self) -> None:
        self._tracked.clear()


def _enrich_ask(span: dict | None, event) -> None:
    """Carry the question structure onto an ask's span.

    Without it the `/live` sheet has no options to render and falls back to
    read-only, so the tier's own questions would be unanswerable.
    """
    if not span or getattr(event, 'tool_name', '') != 'AskUserQuestion':
        return
    questions = ask_questions(getattr(event, 'tool_input', None) or {})
    if questions:
        span['attributes']['questions'] = questions


def _enrich_permission(span: dict | None, event) -> None:
    """Carry what is being asked onto a parked call's span.

    A decision card that can only name the tool asks an operator to approve
    `tool.Bash` with no command in sight, which is not a decision at all.
    """
    if not span or not isinstance(event, PermissionRequested):
        return
    if event.kind == 'question':
        return
    span['attributes'].update(
        policy.request_attrs(event.tool_name, event.tool_input, event.kind))


class AgentRunner:
    """One live session. Not reusable across runs."""

    def __init__(self, trace_id: str, *, cwd: str | None = None,
                 options: client.RunOptions | None = None,
                 resume: str | None = None):
        self.trace_id = trace_id
        self.cwd = cwd
        self.options = options
        # The CLI session this run continues, if any — never this run's own
        # trace id, which the CLI has no session under. A resumed run may reuse
        # the trace id it continues, so `resumed_from` naming that would be
        # self-referential and tell a later reader nothing.
        self.resume = resume
        self.loop: asyncio.AbstractEventLoop | None = None
        self._client = None
        self._tool_names: dict[str, str] = {}
        # tool_use_id → the answered Q&A, held until the tool's result span is
        # emitted — see `_enrich_answer`.
        self._answered_asks: dict[str, dict] = {}
        self._prompts: asyncio.Queue = asyncio.Queue()
        # The same texts, readable from another thread. An SDK run writes no
        # transcript `queue-operation` entries, so this list is the only thing
        # `/live` can derive "still waiting" from — without it a queued steer is
        # represented solely by the client's optimistic echo and vanishes when
        # that TTL lapses, while the prompt is in fact still queued.
        self._pending: list[str] = []
        self._model = settings.agent_sdk.model or None
        self._row_model = self._model
        # The last turn index handed out, not the turn currently running: an
        # index is the usage row's identity, so it is allocated once and never
        # reused.
        self._turn_index = -1
        self._stop_reason = "exited"
        self._stop_status = "exited"
        # The CLI's own name for this session, learned from the first message
        # that carries it — see `_note_session`.
        self._session_id: str | None = None
        self._stopping = asyncio.Event()
        self._stopped = False
        self._queue_closed = False
        self._reader: asyncio.Task | None = None
        # The turn in flight, if any. At most one: see `_open_frame_turn`.
        self._open: list[_Turn] = []
        self._tasks = _TaskLedger()
        # Set once the CLI will send nothing further — see `_stream_closed`.
        self._stream_dead = False
        # Frames seen over the life of the session. Only its movement is read
        # (`_await_prompt`), never its value.
        self._frames = 0

    # ── capture ────────────────────────────────────────────────────────

    async def _post(self, span: dict | None) -> None:
        if not span:
            return
        from lib.hook_plugin import post_span

        await asyncio.to_thread(lambda: post_span(**span))

    async def _emit(self, event) -> None:
        span = to_span(self._name_tool(event))
        _enrich_ask(span, event)
        _enrich_permission(span, event)
        self._enrich_answer(span, event)
        await self._post(span)

    def _enrich_answer(self, span: dict | None, event) -> None:
        """Carry the answered Q&A onto the ask's *resolved* span.

        The pending row is the one holding the questions, and the serve-time
        merge retires it once this row lands — so without both halves here an
        answered question renders as a bare tool row with nothing in it. The
        hook tier's PostToolUse span carries `questions` + `answers`; this is
        the same pair, from the answer regin itself handed back.
        """
        if not span or not isinstance(event, ToolResult):
            return
        answered = self._answered_asks.pop(event.tool_use_id, None)
        if answered:
            span['attributes'].update(answered)

    async def _handle(self, event, turn: _Turn | None) -> None:
        """Route one event to whichever sink records it.

        Everything is a span except token accounting: a per-call `UsageUpdated`
        only advances the context high-water mark, and the turn's totals go to
        `turn_usage` — the table the session aggregates, cost and the context
        meter read.
        """
        if isinstance(event, UsageUpdated):
            if turn is not None:
                turn.context = context_tokens(event.usage)
            return
        if isinstance(event, TurnCompleted):
            await self._ingest_usage(event, turn)
            return
        self._track_model(event)
        await self._emit(self._stamp_turn(event, turn))
        if isinstance(event, TurnFailed):
            # An interrupted turn spent its tokens too, and it has a span AND a
            # usage row — the only event that produces both.
            await self._ingest_usage(event, turn)

    def _track_model(self, event) -> None:
        """Remember the model the agent is answering on, so the turn-usage row
        prices against it rather than the configured default.

        Subagent rows are skipped: a Task running on a cheaper model would
        otherwise reprice the parent's turn. `ingest._handle_transcript_model`
        guards the same way on the read side.
        """
        if getattr(event, 'agent_id', None):
            return
        model = getattr(event, 'model', None)
        if model:
            self._model = model

    def _stamp_turn(self, event, turn: _Turn | None):
        """Stamp the turn identity the hook tier's assistant spans carry.

        `turn_uuid` is what joins a span to its `turn_usage` row and what the
        serve-time ladder groups a turn's rows by; without it these spans have
        no anchor and fall through to the chronological fallback.
        """
        if turn is None or not isinstance(event, (AssistantText,
                                                  AssistantThinking)):
            return event
        return replace(event, turn_uuid=turn_uuid(self.trace_id, turn.index),
                       turn_index=turn.index)

    async def _ingest_usage(self, event, turn: _Turn | None) -> None:
        """A usage roll-up that arrives outside a turn has no row to belong to
        — dropping it beats inventing a turn the session never ran."""
        from lib.hook_plugin import post_event

        if turn is None:
            return
        row = turn_usage_row(event, turn.index, model=self._model,
                             context_used=turn.context)
        await asyncio.to_thread(lambda: post_event('turn_usage', [row]))
        await self._persist_model()

    async def _persist_model(self) -> None:
        """Advance the run row's model once the agent has revealed it.

        `agent_sdk.model` is empty by default, so the row is created with no
        model at all and would keep claiming none for the life of the run while
        its own usage rows name one."""
        if not self._model or self._model == self._row_model:
            return
        self._row_model = self._model
        await asyncio.to_thread(
            lambda: store.upsert_run(self.trace_id, status="running",
                                     model=self._model))

    def _name_tool(self, event):
        """Tool results carry only a `tool_use_id`, so the call's name has to be
        carried forward from the `ToolCall` to name the resolved span."""
        if isinstance(event, ToolCall) and event.tool_use_id:
            self._tool_names[event.tool_use_id] = event.tool_name
            return event
        if isinstance(event, ToolResult) and not event.tool_name:
            name = self._tool_names.pop(event.tool_use_id, '') or 'unknown'
            return replace(event, tool_name=name)
        return event

    # ── the stream ─────────────────────────────────────────────────────

    async def _read_messages(self) -> None:
        """Drain the CLI for the life of the session.

        `receive_messages()` ends only on the SDK's own end sentinel, so this
        outlives every turn — which is the point: the frames a background
        subagent emits after its turn's result share the SDK's bounded message
        stream with the control frames that answer permission asks, and nobody
        else is draining them.

        Whatever ends the stream ends the session with it — see
        `_stream_closed`.
        """
        try:
            async for message in self._client.receive_messages():
                self._frames += 1
                self._note_session(message)
                await self._route(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — reported as the run's end
            log.error("sdk_reader_failed", trace_id=self.trace_id,
                      detail=str(exc))
            self._stream_closed(f"agent stream failed: {exc}")
        else:
            self._stream_closed("agent stream ended")

    def _stream_closed(self, detail: str) -> None:
        """End the session a stream will send nothing more for.

        A turn waits on its result and the prompt queue waits on an operator;
        `idle_timeout_sec` bounds only the second. So a child that exits — or a
        transport the operator's own interrupt closed — would otherwise leave a
        `running` row nothing backs, holding a `max_concurrent_runs` slot and
        accepting steers it can never run.

        Releasing the turns in flight is not enough on its own: a prompt queued
        before the death is still waiting, and running it would open a turn
        nothing can close. So the death is latched, and `_run_turn` reads the
        latch on either side of the one await it takes before opening its turn
        — the prompt's span post. Its remaining awaits need no such read: by
        then the turn is in `_open`, which is what this releases.

        An ending the session was already headed for is not a failure. That is
        an operator's Stop, whose interrupt is what closed the transport, and a
        run whose queue was closed with no turn left unanswered — a `one_shot`
        that answered and exited. Anything else is a child that died on us:
        `_queue_closed` alone would file a one-shot crashed mid-answer as a
        clean exit.
        """
        expected = self._stopping.is_set() or (self._queue_closed
                                               and not self._open)
        self._stream_dead = True
        self._release_open_turns(None if expected else RuntimeError(detail))
        if not expected:
            self._stop_reason = detail
            self._stop_status = "failed"
        # Not `close()`: that reason belongs to an operator's Stop, and this
        # session ended without one.
        self._queue_closed = True
        self._prompts.put_nowait(_CLOSE)

    async def _route(self, message) -> None:
        """Attribute one frame to a turn, then record it.

        The turn is read once for the whole message: a span POST is a thread
        hop, and re-reading it per event tears one model message across two
        turns when the pump drops one in the gap.
        """
        self._tasks.observe(message)
        events = from_sdk_message(self.trace_id, message)
        terminal = any(isinstance(e, (TurnCompleted, TurnFailed))
                       for e in events)
        recorded, turn = self._admit(message, terminal)
        if not recorded:
            return
        for event in events:
            await self._handle(event, turn)
        if terminal and turn is not None:
            self._close_turn(turn)

    def _admit(self, message, terminal: bool) -> tuple[bool, _Turn | None]:
        """Whether this frame is recorded, and the turn it belongs to.

        Only the **main agent's** frames are a turn's. `parent_tool_use_id` is
        the subagent marker (`agent_events.from_sdk`, and the same guard
        `_track_model` reads), and a backgrounded subagent's messages are
        assistant messages like any other — but the CLI owes them no top-level
        result, so opening a turn for one strands the pump on a result that is
        never coming. Their spans are still emitted: `agent_id` is what the
        trace attributes them by. `ResultMessage` carries no
        `parent_tool_use_id` (`claude_agent_sdk.types`), so every result is
        top-level by construction and a subagent can never close a turn.
        """
        if getattr(message, 'parent_tool_use_id', None):
            return True, self._frame_turn()
        if terminal:
            return self._result_turn()
        if type(message).__name__ == 'AssistantMessage':
            self._open_frame_turn()
        return True, self._frame_turn()

    def _frame_turn(self) -> _Turn | None:
        """The turn a non-terminal frame belongs to: the one that is open."""
        return self._open[0] if self._open else None

    def _open_frame_turn(self) -> None:
        """Open a turn for this assistant frame if nothing is running.

        The agent speaks with no prompt behind it — a finished background task
        wakes it for a follow-up turn of its own — and that work is its own,
        not spend billed to whichever prompt ran last. But it gets a turn only
        while the floor is free. Two turns open at once is the state that
        cannot be resolved: a `ResultMessage` carries no turn label, so a CLI
        that answers both with a single result leaves the pump waiting on a
        result nobody owes. Measured against the bundled CLI — a prompt sent 4s
        into a running turn came back as one `result` with `num_turns=2`, and
        the first prompt's answer was never spoken at all.
        """
        if self._open:
            return
        self._open_turn()

    def _result_turn(self) -> tuple[bool, _Turn | None]:
        """Which turn a result ends, if any. Not recorded means dropped.

        Whichever turn is open takes it; only one ever is. A result arriving
        with nothing open is dropped rather than held against the next turn —
        a turn that produced no frames has no spend worth recording, and a
        result that outlives its turn would otherwise end someone else's and
        leave the session one result out of phase for good.
        """
        if self._open:
            return True, self._open[0]
        return False, None

    def _open_turn(self, done: asyncio.Future | None = None) -> _Turn:
        """Start a turn on its own index.

        Allocated before the frame's events are handled so `_stamp_turn` puts
        this turn's `turn_uuid` on its assistant spans; stamping them with the
        previous turn's would file a subagent's work under a turn that ended
        hours earlier.
        """
        turn = _Turn(index=self._turn_index + 1, done=done)
        self._turn_index = turn.index
        self._open.append(turn)
        return turn

    def _close_turn(self, turn: _Turn) -> None:
        """Retire one turn, releasing the pump if it was waiting on it.

        Only a prompted turn carries a `done`, so this is a no-op for the pump
        on any other. Which turn a result ends is `_result_turn`'s decision;
        this only carries it out.
        """
        self._drop_turn(turn)
        if turn.done is not None and not turn.done.done():
            turn.done.set_result(None)

    def _drop_turn(self, turn: _Turn) -> None:
        """Forget a turn nobody is waiting on any more — an abandoned one. Its
        result, if it ever comes, then closes whatever is open in its place."""
        if turn in self._open:
            self._open.remove(turn)

    def _release_open_turns(self, exc: BaseException | None) -> None:
        """Let go of every turn in flight, so the pump is not left waiting on a
        result that can no longer arrive."""
        self._tasks.forget()
        open_turns, self._open = self._open, []
        for turn in open_turns:
            if turn.done is None or turn.done.done():
                continue
            if exc is None:
                turn.done.set_result(None)
            else:
                turn.done.set_exception(exc)

    # ── permissions ────────────────────────────────────────────────────

    async def _can_use_tool(self, tool_name: str, tool_input: dict, context):
        kind = policy.permission_kind(tool_name)
        if kind is None:
            return client.allow(tool_input)
        return await self._park(kind, tool_name, tool_input, context)

    async def _park(self, kind: str, tool_name: str, tool_input: dict, context):
        """Hold the call open until the operator resolves it from `/live`."""
        tool_use_id = getattr(context, "tool_use_id", "") or ""
        await self._emit(PermissionRequested(
            trace_id=self.trace_id,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            tool_input=tool_input,
            kind=kind,
        ))
        await self._notify_park(kind, tool_name, tool_input, tool_use_id)
        try:
            resolution = await self._await_operator(kind, tool_use_id,
                                                    tool_input)
        finally:
            await self._dismiss_park_notice()
        if kind == "question":
            return self._answered(tool_use_id, tool_input, resolution)
        return await self._decided(tool_name, tool_use_id, tool_input,
                                   resolution)

    @property
    def _permission_mode(self) -> str:
        """The mode this run actually launches under, per-run override first."""
        return (self.options.permission_mode if self.options else "") or ""

    async def _notify_park(self, kind: str, tool_name: str, tool_input: dict,
                           tool_use_id: str) -> None:
        """Tell the operator out-of-band that a call is waiting on them.

        A park waits indefinitely, so this push is what keeps that from
        meaning "hangs": without it the only signal a human ever got was a row
        in a trace nobody had open — the hook tier pushes this event, so a
        session regin owns would be the *quiet* one.

        Off the loop thread: the push channels do network I/O and every run on
        this process shares one loop.
        """
        try:
            from lib.agent_messages import event_notify

            attrs = policy.notify_attrs(kind, tool_name, tool_input,
                                        tool_use_id)
            await asyncio.to_thread(
                lambda: event_notify.notify_permission_request(
                    trace_id=self.trace_id, attrs=attrs))
        except Exception:  # noqa: BLE001 — a push must never break the park
            log.error("sdk_park_notify_failed", trace_id=self.trace_id,
                      exc_info=True)

    async def _dismiss_park_notice(self) -> None:
        """Retire the pending card once this session has nothing parked.

        The card is keyed per *session* while parks are keyed per call, so
        dismissing on the first resolution would clear a notice that is still
        true for the calls still waiting.
        """
        try:
            if registry.pending_asks(self.trace_id):
                return
            from lib.agent_messages import event_notify

            await asyncio.to_thread(
                lambda: event_notify.resolve_permission(self.trace_id))
        except Exception:  # noqa: BLE001 — as above; a stale card beats a crash
            log.error("sdk_park_dismiss_failed", trace_id=self.trace_id,
                      exc_info=True)

    async def _await_operator(self, kind: str, tool_use_id: str,
                              tool_input: dict):
        """Park this call and wait for the human — with no clock on it.

        One assistant message can carry several gated calls, so each waits on
        its own future and drops only its own park; the session's other parked
        calls are still someone's to answer.

        A park deliberately never expires. Declining one on a timer refuses a
        call the operator may never have been shown, and the model is told it
        was refused — so the agent goes on to narrate a decision the human did
        not make. An unattended park therefore holds its worker and a
        `max_concurrent_runs` slot until it is answered or the run is stopped.
        """
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        ask_id = registry.register_ask(registry.PendingAsk(
            trace_id=self.trace_id,
            tool_use_id=tool_use_id,
            tool_input=tool_input,
            future=future,
            loop=asyncio.get_running_loop(),
            kind=kind,
        ))
        try:
            return await future
        finally:
            # Every exit drops this park and only this one: resolved (already
            # popped, so a no-op) or cancelled by teardown. A park left behind
            # is a call the operator can still be offered and nothing can
            # deliver an answer to.
            registry.discard_ask(ask_id)

    def _answered(self, tool_use_id: str, tool_input: dict, answers):
        # Reachable only on teardown: nothing else ends a park unresolved.
        if answers is None:
            return client.deny(policy.DISMISSED)
        updated = build_updated_input(tool_input, answers)
        self._answered_asks[tool_use_id] = {
            'questions': (tool_input or {}).get('questions') or [],
            'answers': updated.get('answers') or {},
        }
        return client.allow(updated)

    async def _decided(self, tool_name: str, tool_use_id: str,
                       tool_input: dict, decision):
        """Run or refuse the gated call, and record which of the two happened.

        The resolution span is what a later reader sees: the parked
        `permission.request` placeholder is retired by `tool_use_id`, so
        without this the trace of a denial is a request that simply stops.
        """
        behavior, detail = policy.decision_outcome(decision)
        await self._emit(PermissionResolved(
            trace_id=self.trace_id,
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            behavior=behavior,
            detail=detail,
        ))
        if behavior == "allow":
            return client.allow(tool_input)
        return client.deny(detail)

    # ── lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        others = registry.active_run_count(exclude=self.trace_id)
        if others >= settings.agent_sdk.max_concurrent_runs:
            raise RunnerBusy("max_concurrent_runs reached")
        self.loop = asyncio.get_running_loop()
        shadowed = settings.agent_sdk.shadowed_gating(self._permission_mode)
        if shadowed:
            log.error("sdk_gating_inert", trace_id=self.trace_id,
                      detail=shadowed)
        self._client = client.new_client(
            cwd=self.cwd, can_use_tool=self._can_use_tool,
            options=self.options, resume=self.resume)
        await self._client.connect()
        self._reader = asyncio.ensure_future(self._read_messages())
        registry.register_run(self.trace_id, self)
        # The pid is what makes the reaper safe: it distinguishes a row this
        # process still backs from one a dead process left behind.
        store.upsert_run(self.trace_id, status="running", cwd=self.cwd,
                         model=self._model, pid=os.getpid())
        await self._emit(SessionStarted(
            trace_id=self.trace_id, source="sdk", model=self._model,
            cwd=self.cwd, agent_type="sdk", resumed_from=self.resume,
        ))

    @property
    def is_stopping(self) -> bool:
        """True once this session will run nothing further.

        The registry entry outlives a stop by a turn's teardown, so
        reachability alone would accept prompts this session never runs — and
        a one-shot run, whose queue is closed from the start, would accept a
        follow-up that sits behind the terminator forever.
        """
        return self._stopping.is_set() or self._stopped or self._queue_closed

    @property
    def stop_requested(self) -> bool:
        """True only when someone asked this session to stop — distinct from
        `is_stopping`, which also covers a one-shot run whose queue was closed
        at launch and which therefore *finished* rather than being stopped."""
        return self._stopping.is_set()

    def enqueue(self, text: str, *, waiting: bool = True) -> None:
        """Queue a prompt. Must run on this runner's loop — see
        `registry.submit_prompt` for the cross-thread entry point.

        `waiting=False` for the prompt a run was launched with: it is the
        run's first turn, not a message queued behind one, and reporting it as
        waiting puts a steering chip on the card for the gap between the run
        becoming reachable and its pump starting — which is exactly when an
        operator opens `/live` on a run they just launched.
        """
        self._prompts.put_nowait(text)
        if waiting:
            self._pending.append(text)

    def pending_prompts(self) -> list[str]:
        """Prompts queued behind the running turn, oldest first. Safe to call
        from any thread — a snapshot, never the live list."""
        return list(self._pending)

    def close(self) -> None:
        """End the session once everything already queued has run."""
        self._stop_reason = "stopped"
        self._queue_closed = True
        self._prompts.put_nowait(_CLOSE)

    async def request_stop(self) -> None:
        """Stop now, including from inside a turn — the operator's Stop.

        Closing the queue alone only lands *between* turns, so a turn that
        never completes would leave `stop` a promise nothing keeps: the session
        stays owned, its child process alive, and its slot counted against
        `max_concurrent_runs` until the server restarts. So the interrupt goes
        out to end the stream the pump is draining, and if the CLI doesn't
        honour it `_drain_turn`'s grace period abandons the turn anyway.

        Anything still queued is dropped: someone who pressed Stop did not mean
        "run three more prompts first".
        """
        self._stopping.set()
        self._pending.clear()
        self.close()
        if self._client is None:
            return
        try:
            await self._client.interrupt()
        except Exception as exc:
            log.error("sdk_run_interrupt_failed", trace_id=self.trace_id,
                      detail=str(exc))

    async def _await_prompt(self) -> str | None:
        """Block for the next prompt, raising `TimeoutError` on stream silence.

        With `idle_timeout_sec` 0 — the default — this waits indefinitely: an
        interactive session belongs to the operator until they stop it. A
        configured timeout measures *frames*, not the prompt queue, but frames
        are a weak liveness signal — a background subagent or long shell task
        emits none between task notifications — so a timeout can still reap a
        session whose child is mid-work. That trade is acceptable only for
        unattended runs.
        """
        timeout = int(settings.agent_sdk.idle_timeout_sec or 0)
        if timeout <= 0:
            return await self._prompts.get()
        while True:
            seen = self._frames
            try:
                return await asyncio.wait_for(self._prompts.get(), timeout)
            except asyncio.TimeoutError:
                if self._frames == seen:
                    raise

    async def _next_prompt(self) -> str | None:
        """The next queued prompt, or None when the session should end.

        An idle session still holds a `claude` child process, so an abandoned
        one is reclaimed rather than pinned against `max_concurrent_runs`
        forever.
        """
        try:
            text = await self._await_prompt()
        except asyncio.TimeoutError:
            self._stop_reason = "idle timeout"
            return None
        # The mirror pops with the queue, so a prompt stops reading as "waiting"
        # the moment its turn starts — the same poll the real prompt span lands.
        # `_CLOSE` was never mirrored, so it falls through as a no-op.
        if self._pending and self._pending[0] is text:
            del self._pending[0]
        return text

    async def pump(self) -> None:
        """Run queued prompts one at a time until the session ends."""
        while True:
            text = await self._next_prompt()
            if text is _CLOSE:
                return
            await self._drain_turn(text)
            if self._stopping.is_set():
                return

    async def _drain_turn(self, text: str) -> None:
        """Run one turn, abandoning it if the session is stopped mid-flight.

        A turn ends when the CLI sends its result, and a CLI that sends none
        leaves a turn no interrupt can be proven to end. So a stop that has
        already been asked for is honoured on a timer rather than on the CLI's
        cooperation.
        """
        turn = asyncio.ensure_future(self._run_turn(text))
        stopping = asyncio.ensure_future(self._stopping.wait())
        done, _ = await asyncio.wait({turn, stopping},
                                     return_when=asyncio.FIRST_COMPLETED)
        stopping.cancel()
        if turn in done:
            turn.result()
            return
        await self._abandon_turn(turn)

    async def _abandon_turn(self, turn) -> None:
        """Give an interrupted turn a grace period to end itself, then drop it.

        `wait_for` cancels on timeout, so either way the pump is released.
        """
        grace = int(settings.agent_sdk.stop_grace_sec or 0)
        if grace <= 0:
            turn.cancel()
            return
        try:
            await asyncio.wait_for(turn, grace)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            log.error("sdk_turn_abandoned", trace_id=self.trace_id,
                      detail="turn did not end within stop_grace_sec")
        except Exception as exc:
            log.error("sdk_turn_failed_after_stop", trace_id=self.trace_id,
                      detail=str(exc))

    def _note_session(self, message) -> None:
        """Alias the CLI's session id onto this run the first time it appears.

        The child loads the user's hooks, so it reports a session of its own
        and an operator can open `/live` on *that* id. Without the alias the
        composer there falls through to the tmux bridge — which resolves to
        whatever pane regin's server was started from, not the agent.
        """
        session_id = getattr(message, 'session_id', None)
        if not session_id:
            data = getattr(message, 'data', None)
            session_id = data.get('session_id') if isinstance(data, dict) else None
        if not session_id or session_id == self._session_id:
            return
        self._session_id = session_id
        registry.register_alias(session_id, self.trace_id)
        # The registry's alias is process-local and dies with the server; the
        # serve-time reader that unions the two traces into one session runs
        # long after this process is gone, so the link has to be durable.
        store.set_cli_session(self.trace_id, session_id)

    async def _run_turn(self, text: str) -> None:
        """Send one prompt and wait for the reader to close its turn.

        The turn is opened before the prompt goes out, or a turn the CLI
        answers immediately would resolve nothing and be waited on forever —
        but nothing may be awaited between taking the floor and sending, which
        is why the prompt's own span is posted first. That post is a thread
        hop; a result landing during it belongs to the turn that was speaking
        before this prompt existed, and taking the floor ahead of it would hand
        that result to a prompt the CLI has not yet been given. Sending is a
        yield point too, so that window is narrowed rather than closed — a
        result crossing `query` still ends this turn early. It is bounded by a
        write to the child's stdin instead of by an HTTP POST.

        A prompt queued before the stream died is dropped here rather than in
        the pump: this runs as its own task, scheduled *behind* the reader, so
        the pump can pick a prompt up while the child is still alive and reach
        this line after it has died. The latch is read again after the span
        post for the same reason it is read at all — the child can die during
        it, and `_release_open_turns` cannot save a turn that had not been
        opened yet. Nothing else bounds the wait: a turn's result is not what
        `idle_timeout_sec` covers.
        """
        if self._stream_dead:
            return
        await self._emit(prompt_event(self.trace_id, text))
        if self._stream_dead:
            return
        self._take_the_floor()
        done = asyncio.get_running_loop().create_future()
        turn = self._open_turn(done)
        try:
            await self._client.query(text)
            await done
        finally:
            self._drop_turn(turn)

    def _take_the_floor(self) -> None:
        """Retire whatever turn was running, for the prompt about to go out.

        The CLI folds an inbound prompt into whatever it is already saying and
        answers the pair with one result, so a turn left open would take the
        result this prompt is waiting on and strand the pump forever. Retiring
        it costs that turn nothing: its remaining frames land on this one,
        which is where the CLI has put them anyway. Nothing is waiting on it
        either — the pump is serial, so only a turn the agent opened for itself
        can still be open here, and those carry no `done`.
        """
        for turn in list(self._open):
            self._close_turn(turn)

    async def interrupt(self) -> None:
        await self._client.interrupt()

    async def _settle_stream(self, status: str) -> None:
        """Let an answer still in flight land before the transport closes.

        The pump's turn ending is not proof the conversation is over. A
        finished background task wakes the agent for a follow-up turn of its
        own, which starts after the result the pump was waiting on. Tearing
        down on that result disconnects a child that is still writing: the
        trace keeps a `prompt` with no reply, and a `one_shot` run returns
        nothing to its caller under a row that reads `completed`.

        So a graceful ending waits for the stream to go quiet first. What
        counts as quiet depends on whether the agent still has delegated work
        running: measured against a live child, the gap between a task settling
        and the wake turn's first frame was 6.4s, and 15% of that run's gaps
        exceeded a quarter-second. A lull is therefore only taken as the end of
        the conversation once nothing is in flight — while a task is still
        running the wait runs to the `stop_grace_sec` deadline, because the
        child has work it can still speak about.

        A crashed run has nothing to wait for, a dead stream has nothing left
        to send, and an operator's Stop asked for the session to end rather
        than for one more answer.
        """
        window = float(settings.agent_sdk.teardown_settle_sec or 0)
        if self._waits_for_the_child(status, window):
            await self._await_quiet(window)

    def _waits_for_the_child(self, status: str, window: float) -> bool:
        """Whether this ending waits at all — see `_settle_stream`."""
        return not (window <= 0 or status != "exited" or self._reader is None
                    or self._stopping.is_set() or self._stream_dead)

    async def _await_quiet(self, window: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(float(settings.agent_sdk.stop_grace_sec
                                           or 0), window)
        while loop.time() < deadline:
            seen = self._frames
            await asyncio.sleep(window)
            if self._frames == seen and not self._tasks.in_flight:
                return

    async def _close_reader(self) -> None:
        """Stop draining before the session's last span is written, so nothing
        the child is still emitting lands after `session.end`."""
        reader, self._reader = self._reader, None
        if reader is None:
            return
        reader.cancel()
        try:
            await reader
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 — teardown must still finish
            log.error("sdk_reader_teardown_failed", trace_id=self.trace_id,
                      detail=str(exc))

    async def stop(self, *, status: str = "", detail: str = "") -> None:
        """Tear the session down. Idempotent, and the row is written even when
        teardown fails part-way: a `running` row nothing backs is the one state
        that outlives the process and misleads every later reader.

        The caller names the status only when it knows better than the session
        does; a session whose child died already knows it failed.
        """
        if self._stopped:
            return
        self._stopped = True
        status = status or self._stop_status
        reason = detail or self._stop_reason
        registry.unregister_run(self.trace_id)
        # A park cancelled with the session never reaches its own dismissal, so
        # without this a dead session can leave a "waiting on you" card standing
        # — the one moment the notice is most misleading.
        await self._dismiss_park_notice()
        await self._settle_stream(status)
        await self._close_reader()
        if self._client is None:
            # Refused before it ever connected (at capacity): there is no
            # session to close, so `session.end` would bracket nothing.
            store.upsert_run(self.trace_id, status=status, detail=reason)
            return
        try:
            await self._emit(SessionEnded(trace_id=self.trace_id, reason=reason))
            await self._client.disconnect()
        finally:
            store.upsert_run(self.trace_id, status=status, detail=reason)


async def run_session(trace_id: str, prompt: str, *,
                      cwd: str | None = None,
                      options: client.RunOptions | None = None,
                      one_shot: bool = False,
                      resume: str | None = None) -> None:
    """Launch and run `prompt`.

    Stays open for follow-ups until stopped, unless `one_shot` — a run regin
    made to get one job done must end with its turn, or it holds a
    `max_concurrent_runs` slot until `idle_timeout_sec` elapses, long after
    the caller has its result.

    `resume` continues an earlier session's conversation instead of starting
    an empty one, which is what makes a session the user drove in a terminal
    steerable later from `/live`.
    """
    if not settings.agent_sdk.enabled:
        raise RuntimeError("agent_sdk disabled")
    runner = AgentRunner(trace_id, cwd=cwd, options=options, resume=resume)
    # Queued before the session is reachable, so a follow-up arriving during
    # `start()` cannot overtake the prompt the run was launched for.
    #
    # A launch may carry no prompt at all — opening the session is the act,
    # and the next turn comes from the card's composer. The pump then waits on
    # an empty queue, which is the same state it reaches between any two turns,
    # so `idle_timeout_sec` still reclaims one nobody comes back to.
    if prompt.strip():
        runner.enqueue(prompt, waiting=False)
    if one_shot:
        # The terminator queues behind the prompt, so the pump stops at the
        # end of that turn rather than waiting out the idle timeout.
        runner.close()
    try:
        await runner.start()
        await runner.pump()
    except Exception as exc:
        # Covers `start()` too: anything raising after `connect()` would
        # otherwise leave a connected child nobody disconnects and a row
        # stuck at `running` until the next boot's reaper.
        log.error("sdk_run_failed", trace_id=trace_id, detail=str(exc))
        await runner.stop(status="failed", detail=str(exc))
        raise
    # A one-shot run's queue was closed from the start, so its stop reason
    # reads "stopped" even when it simply finished; only an operator's Stop
    # sets `is_stopping`.
    await runner.stop(
        detail="" if not one_shot
        else ("stopped" if runner.stop_requested else "completed"))
