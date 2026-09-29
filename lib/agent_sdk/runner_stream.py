"""The session-long stream reader: routes every CLI frame to the open turn,
records usage and model, and settles the stream on stop.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from lib.agent_events import (
    AssistantText,
    AssistantThinking,
    PromptDelivered,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    UsageUpdated,
    turn_usage_row,
)
from lib.agent_events.from_sdk import from_sdk_message
from lib.agent_events.usage import context_tokens, turn_uuid
from lib.settings import settings
from lib.trace.pending_spans import prompt_placeholder_id
from . import store
from .runner_support import _CLOSE, _Turn, log


class _StreamMixin:
    """Stream reading and turn routing for `AgentRunner`."""

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
        if isinstance(event, PromptDelivered) and not self._prompt_expected(event):
            return
        self._track_model(event)
        await self._emit(self._stamp_turn(event, turn), turn)
        if isinstance(event, TurnFailed):
            # An interrupted turn spent its tokens too, and it has a span AND a
            # usage row — the only event that produces both.
            await self._ingest_usage(event, turn)

    def _prompt_expected(self, event: PromptDelivered) -> bool:
        """Whether this delivery echo answers a prompt regin submitted.

        Matching consumes the id: each submit is answered by exactly one echo,
        and the same text re-submitted later re-registers it. An echo that
        matches nothing is injected harness content riding a user-role message,
        not a prompt — dropping it loses nothing, since a real prompt whose
        echo never matches still renders from its placeholder (or the child
        session's own hook-written anchor) via the serve-time merge.
        """
        placeholder = prompt_placeholder_id(self.trace_id, event.text)
        if placeholder not in self._submitted_prompt_ids:
            return False
        self._submitted_prompt_ids.discard(placeholder)
        return True

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

    def _name_tool(self, event, turn: "_Turn | None" = None):
        """Tool results carry only a `tool_use_id`, so the call's name has to be
        carried forward from the `ToolCall` to name the resolved span.

        `_tool_names` outlives the turn that made the call on purpose: a
        result can arrive after its turn was abandoned (`_leave_a_sink`), and
        the span it produces still has to be named. The turn's own copy is the
        one that answers "what is running now", and it is dropped with the
        turn rather than kept until a result that may never come."""
        if isinstance(event, ToolCall) and event.tool_use_id:
            self._tool_names[event.tool_use_id] = event.tool_name
            if turn is not None:
                turn.tools[event.tool_use_id] = event.tool_name
            return event
        if isinstance(event, ToolResult) and not event.tool_name:
            name = self._tool_names.pop(event.tool_use_id, '') or 'unknown'
            if turn is not None:
                turn.tools.pop(event.tool_use_id, None)
            return replace(event, tool_name=name)
        return event

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
        self._retries.observe(message)
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

    def _open_turn(self, done: asyncio.Future | None = None, *,
                   sink: bool = False) -> _Turn:
        """Start a turn on its own index.

        Allocated before the frame's events are handled so `_stamp_turn` puts
        this turn's `turn_uuid` on its assistant spans; stamping them with the
        previous turn's would file a subagent's work under a turn that ended
        hours earlier.
        """
        turn = _Turn(index=self._turn_index + 1, done=done, sink=sink)
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
        the conversation once nothing is in flight.

        `stop_grace_sec` bounds the *silence*, never the work. A delegated
        agent routinely runs for minutes — one measured search subagent was
        18s into its run when a 10s grace expired, and the teardown that
        followed disconnected its parent: the one-shot run was filed
        `exited`/"completed" (settling its task as done), and the killed child
        never reached `SubagentStop`, leaving a marker-less agent the roster
        reads as running. So the deadline is restarted for as long as the
        ledger holds something, which cannot wait forever: only the task types
        that reliably reach a terminal status are tracked (`_TaskLedger`).

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
        grace = max(float(settings.agent_sdk.stop_grace_sec or 0), window)
        deadline = loop.time() + grace
        while loop.time() < deadline:
            seen = self._frames
            await asyncio.sleep(window)
            if self._tasks.in_flight:
                deadline = loop.time() + grace
            elif self._frames == seen:
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
