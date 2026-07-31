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

Spans are posted over HTTP by `lib.hook_plugin.post_span`, which is blocking, so
every write is pushed to a worker thread — a blocked event loop would stall the
`can_use_tool` callback the answer path depends on.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace

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
        # The session this run continues, if any. It is *not* this run's trace
        # id: whether `--resume` keeps the CLI's own session id is that build's
        # business, so the continuation gets its own trace and names its parent
        # instead of assuming it inherits one.
        self.resume = resume
        self.loop: asyncio.AbstractEventLoop | None = None
        self._client = None
        self._tool_names: dict[str, str] = {}
        self._prompts: asyncio.Queue = asyncio.Queue()
        self._model = settings.agent_sdk.model or None
        self._row_model = self._model
        # The turn currently draining. Also the usage row's identity, so it is
        # assigned once per prompt rather than derived from a running count.
        self._turn_index = -1
        # Prompt size of the most recent API call — the honest context figure
        # for a turn that made several. Reset per turn.
        self._call_context: int | None = None
        self._stop_reason = "exited"
        # The CLI's own name for this session, learned from the first message
        # that carries it — see `_note_session`.
        self._session_id: str | None = None
        self._stopping = asyncio.Event()
        self._stopped = False
        self._queue_closed = False

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
        await self._post(span)

    async def _handle(self, event) -> None:
        """Route one event to whichever sink records it.

        Everything is a span except token accounting: a per-call `UsageUpdated`
        only advances the context high-water mark, and the turn's totals go to
        `turn_usage` — the table the session aggregates, cost and the context
        meter read.
        """
        if isinstance(event, UsageUpdated):
            self._call_context = context_tokens(event.usage)
            return
        if isinstance(event, TurnCompleted):
            await self._ingest_usage(event)
            return
        self._track_model(event)
        await self._emit(self._stamp_turn(event))
        if isinstance(event, TurnFailed):
            # An interrupted turn spent its tokens too, and it has a span AND a
            # usage row — the only event that produces both.
            await self._ingest_usage(event)

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

    def _stamp_turn(self, event):
        """Stamp the turn identity the hook tier's assistant spans carry.

        `turn_uuid` is what joins a span to its `turn_usage` row and what the
        serve-time ladder groups a turn's rows by; without it these spans have
        no anchor and fall through to the chronological fallback.
        """
        if not isinstance(event, (AssistantText, AssistantThinking)):
            return event
        return replace(event, turn_uuid=turn_uuid(self.trace_id,
                                                  self._turn_index),
                       turn_index=self._turn_index)

    async def _ingest_usage(self, event) -> None:
        """A usage roll-up that arrives outside a turn has no row to belong to
        — dropping it beats inventing a turn the session never ran."""
        from lib.hook_plugin import post_event

        if self._turn_index < 0:
            return
        row = turn_usage_row(event, self._turn_index, model=self._model,
                             context_used=self._call_context)
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
            return self._answered(tool_input, resolution)
        return await self._decided(tool_name, tool_use_id, tool_input,
                                   resolution)

    @property
    def _permission_mode(self) -> str:
        """The mode this run actually launches under, per-run override first."""
        return (self.options.permission_mode if self.options else "") or ""

    async def _notify_park(self, kind: str, tool_name: str, tool_input: dict,
                           tool_use_id: str) -> None:
        """Tell the operator out-of-band that a call is waiting on them.

        Without this the tier can hold a call for the whole
        `park_timeout_sec` and then decline it, while the only signal a human
        ever got was a row in a trace nobody had open — the hook tier pushes
        this event, so a session regin owns would be the *quiet* one.

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

    async def _wait_for_operator(self, future):
        """Wait for a decision, but not forever when nobody is watching.

        `idle_timeout_sec` cannot cover this: it bounds the wait *between*
        turns, and a park lives inside one. An unattended run — regin's own
        spawns, which no operator has `/live` open for — would otherwise hold
        its worker, its child process and a `max_concurrent_runs` slot until
        the server restarted. Timing out declines the call rather than
        approving it: nobody said yes.
        """
        timeout = int(settings.agent_sdk.park_timeout_sec or 0)
        if timeout <= 0:
            return await future
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            log.write("sdk_park_timed_out", trace_id=self.trace_id,
                      timeout_sec=timeout)
            return None

    async def _await_operator(self, kind: str, tool_use_id: str,
                              tool_input: dict):
        """Park this call and wait. One assistant message can carry several
        gated calls, so each waits on its own future and drops only its own
        park — the session's other parked calls are still someone's to answer.
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
            return await self._wait_for_operator(future)
        finally:
            # Every exit drops this park and only this one: resolved (already
            # popped, so a no-op), timed out, or cancelled. A park left behind
            # is a call the operator can still be offered and nothing can
            # deliver an answer to.
            registry.discard_ask(ask_id)

    def _answered(self, tool_input: dict, answers):
        if answers is None:
            return client.deny("Dismissed by operator")
        return client.allow(build_updated_input(tool_input, answers))

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
        if registry.active_run_count() >= settings.agent_sdk.max_concurrent_runs:
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

    def enqueue(self, text: str) -> None:
        """Queue a prompt. Must run on this runner's loop — see
        `registry.submit_prompt` for the cross-thread entry point."""
        self._prompts.put_nowait(text)

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
        self.close()
        if self._client is None:
            return
        try:
            await self._client.interrupt()
        except Exception as exc:
            log.error("sdk_run_interrupt_failed", trace_id=self.trace_id,
                      detail=str(exc))

    async def _next_prompt(self) -> str | None:
        """The next queued prompt, or None when the session should end.

        An idle session still holds a `claude` child process, so an abandoned
        one is reclaimed rather than pinned against `max_concurrent_runs`
        forever. Set `idle_timeout_sec` to 0 to wait indefinitely.
        """
        timeout = int(settings.agent_sdk.idle_timeout_sec or 0)
        if timeout <= 0:
            return await self._prompts.get()
        try:
            return await asyncio.wait_for(self._prompts.get(), timeout)
        except asyncio.TimeoutError:
            self._stop_reason = "idle timeout"
            return None

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

        `receive_response()` ends when the CLI sends a result — the SDK is
        explicit that without one the iterator continues indefinitely. That is
        a turn no interrupt can be proven to end, so a stop that has already
        been asked for is honoured on a timer rather than on the CLI's
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

    async def _run_turn(self, text: str) -> None:
        """Send one prompt and drain the turn it produces."""
        self._turn_index += 1
        self._call_context = None
        await self._emit(prompt_event(self.trace_id, text))
        await self._client.query(text)
        async for message in self._client.receive_response():
            self._note_session(message)
            for event in from_sdk_message(self.trace_id, message):
                await self._handle(event)

    async def interrupt(self) -> None:
        await self._client.interrupt()

    async def stop(self, *, status: str = "exited", detail: str = "") -> None:
        """Tear the session down. Idempotent, and the row is written even when
        teardown fails part-way: a `running` row nothing backs is the one state
        that outlives the process and misleads every later reader."""
        if self._stopped:
            return
        self._stopped = True
        reason = detail or self._stop_reason
        registry.unregister_run(self.trace_id)
        # A park cancelled with the session never reaches its own dismissal, so
        # without this a dead session can leave a "waiting on you" card standing
        # — the one moment the notice is most misleading.
        await self._dismiss_park_notice()
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
    runner.enqueue(prompt)
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
