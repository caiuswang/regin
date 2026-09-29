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
import threading
import time

from lib.agent_events import (
    SessionEnded,
    SessionStarted,
    ToolResult,
    to_span,
)
from lib.agent_events.from_sdk import prompt_event
from lib.settings import settings
from lib.trace import alias
from lib.trace.pending_spans import prompt_placeholder_id
from . import client, registry, store
from .runner_park import _ParkMixin
from .runner_queue import _QueueMixin
from .runner_stream import _StreamMixin
from .runner_support import (
    _CLOSE,
    RetryState,
    RunnerBusy,
    _enrich_ask,
    _enrich_permission,
    _live_verb,
    _query_payload,
    _Queued,
    _retry_fields,
    _RetryLatch,
    _TaskLedger,
    _Turn,
    log,
)


class AgentRunner(_StreamMixin, _ParkMixin, _QueueMixin):
    """One live session. Not reusable across runs."""

    def __init__(self, trace_id: str, *, cwd: str | None = None,
                 options: client.RunOptions | None = None,
                 resume: str | None = None,
                 prompt_announced: bool = False):
        # One-shot latch: the launcher already posted the launch prompt's span
        # (`supervisor._announce_prompt`), so the first turn must not post a
        # second. The pump is serial and FIFO, and the launch prompt is queued
        # before the session is even reachable, so "the first turn" is exactly
        # the prompt that was announced.
        self._prompt_announced = prompt_announced
        # Placeholder ids of prompts regin has sent whose delivery echo has not
        # come back yet. The transcript also carries user-role entries nobody
        # submitted — a Skill invocation delivers its body as one — and
        # recording those as `prompt` rows plants a bogus turn anchor that the
        # serve-time orphan graft then files real spans under. Only an echo
        # whose text hashes back to one of these ids is a prompt.
        self._submitted_prompt_ids: set[str] = set()
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
        # The same records, readable from another thread. An SDK run writes no
        # transcript `queue-operation` entries, so this list is the only thing
        # `/live` can derive "still waiting" from — without it a queued steer is
        # represented solely by the client's optimistic echo and vanishes when
        # that TTL lapses, while the prompt is in fact still queued.
        self._pending: list[_Queued] = []
        # Guards `_pending` and the records' flags — the only runner state a
        # Flask thread mutates directly. Editing a queued prompt is a list walk
        # and a field write, so it answers in-thread under this lock rather than
        # hopping onto the loop and waiting on a future for an answer the
        # operator is holding the UI open for. Never held across an `await`.
        self._queue_lock = threading.Lock()
        self._queue_seq = 0
        # The per-run override wins over the install default: it is the model
        # this run was actually launched on, and it is known here — before the
        # first assistant message reveals one. Seeding it means the run row and
        # `session.start` carry a model from the first byte instead of claiming
        # none (or the wrong global default) until the transcript names it. An
        # alias like `opus` is refined to the concrete id by `_track_model`, and
        # ingest's `_is_less_specific_model` lets that id replace the alias.
        self._model = (options.model if options else "") \
            or settings.agent_sdk.model or None
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
        # The trace this run's SDK-stream spans land on. Its own id while it
        # drives the run's first child, a per-child segment for every heir a
        # `/clear` opens after that — see `alias.sdk_span_trace_id`.
        # Seeded from `resume` rather than left bare until the first message:
        # the pump can send the resume's prompt before any message has named
        # the child, and that prompt's span would land in whichever
        # conversation the run started with — for a resume onto an heir, the
        # cleared one.
        self._span_trace_id = self._trace_for_child(
            resume, store.cli_session_for(trace_id))
        self._stopping = asyncio.Event()
        # Set by `interrupt()` and cleared once the turn it cancelled is gone.
        # A stop ends the session; this one only ends the turn, so the pump has
        # to be able to tell the two apart when it stops waiting.
        self._interrupting = asyncio.Event()
        self._stopped = False
        self._queue_closed = False
        self._reader: asyncio.Task | None = None
        # The turn in flight, if any. At most one: see `_open_frame_turn`.
        self._open: list[_Turn] = []
        self._tasks = _TaskLedger()
        self._retries = _RetryLatch()
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

        # Events name the run; the spans belong to the conversation it is
        # driving right now, which is a different trace after a `/clear`.
        if span.get('trace_id') == self.trace_id:
            span = {**span, 'trace_id': self._span_trace_id}
        await asyncio.to_thread(lambda: post_span(**span))

    async def _emit(self, event, turn: "_Turn | None" = None) -> None:
        span = to_span(self._name_tool(event, turn))
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
            options=self.options, resume=self.resume,
            trace_id=self.trace_id)
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
    def turn_in_flight(self) -> bool:
        """True while the agent has a turn open — the honest "working".

        A turn the agent opened for itself counts: a finished background task
        wakes it for a follow-up nobody prompted, and an operator watching the
        card is owed "running" for that too. A sink does not — it is a
        placeholder for a result an abandoned turn may never send, and counting
        it would pin the card at "running" until the operator's next prompt
        happened to clear it.
        """
        return any(not turn.sink for turn in self._open)

    @property
    def delegated_work_in_flight(self) -> bool:
        """True while a background agent or workflow this run delegated is
        still running — after the parent's own turn has closed.

        The parent is then sitting at the prompt, but the session is not idle:
        the delegated task is working and will wake the parent when it lands.
        """
        return self._tasks.in_flight

    @property
    def retrying(self) -> "RetryState | None":
        """The failed API call this run is waiting to retry, if it is.

        Scoped to a turn in flight: the latch clears on the CLI's next word,
        and a result is one — but an ABANDONED turn (an interrupt the CLI
        honoured silently) never sends that word, so an unscoped latch would
        report a run parked at the prompt as reconnecting for the rest of its
        life.
        """
        return self._retries.state if self.turn_in_flight else None

    def live_state(self):
        """This run's status now, in the shape the terminal tier reads off a
        pane (`agent_bridge.screen_state.ScreenState`).

        A run's child is spawned with the bridge off and never registers a
        pane, so no screen exists to capture for it. The runner is the only
        thing that knows, and answering in the screen's own shape is what lets
        the card and the session picker render both tiers through one path.

        Read from a Flask thread while the loop mutates what it reads, so each
        container is copied once and every field is taken from that copy —
        `busy` with no turn behind it to time is exactly the inconsistency two
        separate lookups would show. Fields this tier does not carry stay None
        rather than defaulted: streamed tokens and context percent are the
        screen's, not the runner's.

        The tool is the newest call the CURRENT turn still has open — the one
        an operator watching the card is waiting on. Scoped to the turn
        because a call can outlive it unresolved; see `_name_tool`.
        """
        from lib.agent_bridge.screen_state import ScreenState
        turn = next((t for t in list(self._open) if not t.sink), None)
        tools = list(turn.tools.values()) if turn else []
        # Read once, beside the turn it is scoped to, for the same reason every
        # other field here is: `retrying` re-reads `turn_in_flight`, and a turn
        # that ends between the two lookups yields a busy screen carrying a
        # retry banner for a turn that is no longer running.
        retry = self._retries.state if turn else None
        delegated = self._tasks.in_flight
        return ScreenState(
            busy=turn is not None or delegated,
            verb=_live_verb(turn is not None, delegated),
            elapsed_sec=(max(0, int(time.monotonic() - turn.started))
                         if turn else None),
            tool=tools[-1] if tools else None,
            session_id=self._session_id,
            cwd=self.cwd,
            model=self._model,
            **_retry_fields(retry),
        )

    @property
    def stop_requested(self) -> bool:
        """True only when someone asked this session to stop — distinct from
        `is_stopping`, which also covers a one-shot run whose queue was closed
        at launch and which therefore *finished* rather than being stopped."""
        return self._stopping.is_set()

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
        "run three more prompts first". Clearing the mirror is not enough for
        that — the records are already inside the queue — so each is cancelled
        as well, the same flag `cancel_pending` sets, and the pump skips them if
        it reaches them before the terminator.
        """
        self._stopping.set()
        with self._queue_lock:
            for item in self._pending:
                item.cancelled = True
        self._drain_pending("stopped_by_operator")
        self.close()
        if self._client is None:
            return
        try:
            await self._client.interrupt()
        except Exception as exc:
            log.error("sdk_run_interrupt_failed", trace_id=self.trace_id,
                      detail=str(exc))

    async def pump(self) -> None:
        """Run queued prompts one at a time until the session ends."""
        while True:
            item = await self._next_prompt()
            if item is _CLOSE:
                return
            await self._drain_turn(item)
            if self._stopping.is_set():
                return

    async def _drain_turn(self, item: "_Queued") -> None:
        """Run one turn, abandoning it if the turn or the session is cancelled.

        A turn ends when the CLI sends its result, and a CLI that sends none
        leaves a turn no interrupt can be proven to end. So a cancellation that
        has already been asked for is honoured on a timer rather than on the
        CLI's cooperation.

        Both cancellations wait the same way and differ only in what they leave
        behind: a stop ends the pump, while an interrupt returns to it, so the
        session stays up and the prompts queued behind the cancelled turn still
        run. Without the interrupt arm the pump would sit on `await done`
        forever for a CLI that honours an interrupt silently — a session alive
        on paper that will never run another turn.
        """
        # An interrupt asked for while nothing was running has nothing to
        # cancel, and a latch left raised would abandon this turn before it had
        # said anything. Dropping it here also costs an interrupt racing the
        # turn's start nothing: the CLI was sent one either way, and its result
        # ends the turn through the ordinary path.
        self._interrupting.clear()
        turn = asyncio.ensure_future(self._run_turn(item))
        waits = [asyncio.ensure_future(e.wait())
                 for e in (self._stopping, self._interrupting)]
        done, _ = await asyncio.wait({turn, *waits},
                                     return_when=asyncio.FIRST_COMPLETED)
        for wait in waits:
            wait.cancel()
        if turn in done:
            turn.result()
            return
        await self._abandon_turn(turn)
        if not self._stopping.is_set():
            self._leave_a_sink()

    def _leave_a_sink(self) -> None:
        """Hold the floor for a result the abandoned turn is still owed.

        Only after an *interrupt*, because only then does the pump go on to
        another turn. An abandoned turn is one the CLI never ended, so its
        result may still be coming; `_result_turn` gives a result to whatever
        is open, and with the next turn open by then it would close that one
        instead — the "one result out of phase for good" `_result_turn`'s own
        docstring warns about, previously unreachable because every
        abandonment was a stop and the pump returned.

        A sink carries no `done`, so nothing waits on it and a result that
        never arrives strands nothing; the next prompt's `_take_the_floor`
        retires it regardless. Frames arriving meanwhile are the abandoned
        turn's, and attributing them here is what the sink is for.
        """
        if self._open:
            return
        self._open_turn(sink=True)

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
        self._span_trace_id = self._trace_for_child(
            session_id, store.set_cli_session(self.trace_id, session_id))

    def _trace_for_child(self, child: str | None,
                         first_child: str | None) -> str:
        """Where this run's spans go while it drives `child`.

        Asked of the run's recorded first child rather than of whether this
        process has seen one, because a resume reuses the run's trace id: its
        very first child can be an heir an earlier process left behind.
        """
        if not child or not first_child or first_child == child:
            return self.trace_id
        return alias.sdk_span_trace_id(self.trace_id, child)

    async def _run_turn(self, item: "_Queued") -> None:
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
        text = item.text
        if self._stream_dead:
            self._report_drop(item, "stream_dead_before_span")
            return
        self._submitted_prompt_ids.add(
            prompt_placeholder_id(self.trace_id, text))
        if self._prompt_announced:
            self._prompt_announced = False
        else:
            await self._emit(prompt_event(self.trace_id, text))
        if self._stream_dead:
            # The worst of the two: the prompt's span is already posted, so the
            # trace shows the prompt while the CLI was never given it.
            self._report_drop(item, "stream_dead_after_span")
            return
        self._take_the_floor()
        done = asyncio.get_running_loop().create_future()
        turn = self._open_turn(done)
        try:
            log.write("sdk_prompt_sent", trace_id=self.trace_id,
                      prompt_id=item.id, row_id=item.row_id, turn=turn.index)
            await self._client.query(_query_payload(text, item.images))
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
        """Cancel the turn in flight; the session and its queue survive.

        The latch is raised before the interrupt goes out so `_drain_turn` is
        already watching when the CLI honours it — and so a CLI that honours it
        without a `ResultMessage` still releases the pump.
        """
        self._interrupting.set()
        await self._client.interrupt()

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
                      resume: str | None = None,
                      images: list | None = None,
                      prompt_announced: bool = False) -> None:
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
    runner = AgentRunner(trace_id, cwd=cwd, options=options, resume=resume,
                         prompt_announced=prompt_announced)
    # Queued before the session is reachable, so a follow-up arriving during
    # `start()` cannot overtake the prompt the run was launched for.
    #
    # A launch may carry no prompt at all — opening the session is the act,
    # and the next turn comes from the card's composer. The pump then waits on
    # an empty queue, which is the same state it reaches between any two turns,
    # so `idle_timeout_sec` still reclaims one nobody comes back to.
    if prompt.strip():
        runner.enqueue(prompt, waiting=False, images=images)
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
