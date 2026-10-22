"""Launch a task run through a provider's ACP channel (feature #140).

`launch_run` mirrors `lib.agent_sdk.supervisor.launch_run`'s contract: the
call returns a handle before the turn runs, and the handle settles a
`RunOutcome` when the run can no longer finish on its own terms. Unlike the
SDK arm there is no trace-id placeholder or alias group — `session/new`
answers with the session id the child will be traced under before the prompt
is even sent, so the handle's `trace_id` is the real one from birth.

The handle settles (D6) only on a failure: a turn ending with a stopReason
other than `end_turn`, or the channel closing. A turn ending `end_turn` is
not the work being done — the agent's `task finish` report is, and it
arrives through `lib.tasks.runs.finish_run`, not through this handle — so the
session stays open, steerable, until that report or a close. The handle's
outcome is therefore always `failed`; the attempt's `ended_at` guard lets a
report written first win over it.

Feature #141 makes the run steerable while it lives: the handle registers
itself (`lib.agent_acp.registry`) before the turn thread starts, so the
ownership layer sees the whole live window, and it carries a FIFO prompt
queue the web UI's steers land on. The turn thread is a serial pump — the
launch prompt is turns[0], then each queued prompt runs as its own
`session/prompt` on the same session, one at a time, awaited to its
stopReason before the next is sent. The wire itself has no queueing: a
prompt arriving mid-turn is rejected with `turn.agent_busy`, which is why
the pump must never send concurrently.
"""

from __future__ import annotations

import itertools
import os
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import NamedTuple

from lib.activity_log import get_activity_logger
from lib.settings import settings

from . import registry as acp_registry
from .activity import AcpActivity
from .client import (AcpClient, AcpError, AcpLaunchRefused, AcpTurnBusy)
from .permissions import PermissionParks

log = get_activity_logger("agent_acp")

# Pacing for a queued prompt the server rejects as turn.agent_busy anyway
# (a cancel or a slow turn-close can race the next send). The retry doubles
# up to the cap so a server that stays busy is retried, never spun.
_BUSY_RETRY_SEC = 0.25
_BUSY_RETRY_MAX_SEC = 4.0

END_TURN = "end_turn"


class LaunchRefused(RuntimeError):
    """Structured refusal — binary missing, or a param the channel can't honor."""


@dataclass(frozen=True)
class RunOutcome:
    """How a run ended, in the same vocabulary as the `agent_runs` row."""

    trace_id: str
    status: str
    detail: str = ""


class _QueuedPrompt(NamedTuple):
    """One steer waiting behind the current turn."""

    id: str
    text: str


class AcpRunHandle:
    """The completion signal for a channel-launched run (see
    `supervisor.RunHandle` for the contract this mirrors)."""

    def __init__(self, trace_id: str, future: Future,
                 client: AcpClient, session_id: str,
                 cwd: str | None = None, model: str = "") -> None:
        self.trace_id = trace_id
        self._future = future
        self._client = client
        self._session_id = session_id
        self._cwd = cwd
        self._model = model
        self._activity = AcpActivity()
        self.permissions = PermissionParks(trace_id)
        # Every turn that ran, launch turn first. Written by the pump thread
        # only; readers poll it from other threads (a list append is atomic
        # under the GIL, and each outcome is a frozen dataclass).
        self.turns: list[RunOutcome] = []
        self._cond = threading.Condition()
        self._queue: list[_QueuedPrompt] = []
        self._prompt_ids = itertools.count(1)
        self._closed = False
        self._ending = False
        self._settled = False
        self._in_flight = False
        self._close_callbacks: list = []
        self._torn_down = False

    # ── completion: settles once, on a failed turn or the close (D6) ──

    def done(self) -> bool:
        return self._future.done()

    def wait(self, timeout: float | None = None) -> RunOutcome:
        try:
            return self._future.result(timeout)
        except TimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001 — a crash is a failed run
            return RunOutcome(self.trace_id, "failed", str(exc))

    def add_done_callback(self, callback) -> None:
        self._future.add_done_callback(
            lambda f: callback(f.result() if not f.cancelled()
                               else RunOutcome(self.trace_id, "failed",
                                               "cancelled")))

    def add_close_callback(self, callback) -> None:
        """Call `callback()` once the channel is closed — later than the done
        callbacks, which fire on the first failed turn while an interrupted
        session is still open. Runs at once on a handle already closed."""
        with self._cond:
            if not self._torn_down:
                self._close_callbacks.append(callback)
                return
        callback()

    def _settle(self, outcome: RunOutcome) -> None:
        """Settle the future once. The flag is taken under the lock and the
        result set outside it: the done callbacks run on this thread, and
        they must not run holding the queue's lock."""
        with self._cond:
            if self._settled:
                return
            self._settled = True
        self._future.set_result(outcome)

    # ── steer queue ──────────────────────────────────────────────

    def enqueue(self, text: str) -> bool:
        """Queue a follow-up prompt behind the current turn. False once the
        handle is closed — the prompt has nowhere to run."""
        with self._cond:
            if self._closed or self._ending:
                return False
            self._queue.append(_QueuedPrompt(f"acp-{next(self._prompt_ids)}",
                                             text))
            self._cond.notify()
            return True

    def queued_prompts(self) -> list[dict]:
        """`{id, text}` for each prompt waiting behind the current turn."""
        with self._cond:
            return [{"id": q.id, "text": q.text} for q in self._queue]

    def cancel_queued(self, prompt_id: str) -> bool:
        with self._cond:
            for i, q in enumerate(self._queue):
                if q.id == prompt_id:
                    self._queue.pop(i)
                    return True
            return False

    def edit_queued(self, prompt_id: str, text: str) -> tuple[bool, str]:
        with self._cond:
            for i, q in enumerate(self._queue):
                if q.id == prompt_id:
                    self._queue[i] = _QueuedPrompt(q.id, text)
                    return True, ""
        return False, "that prompt is no longer queued"

    def _take(self, block: bool) -> _QueuedPrompt | None:
        """Pop the next queued steer. None when closed or ending, or —
        non-blocking — when the queue is empty. `close()` and
        `end_after_turn()` wake a blocked pump."""
        with self._cond:
            while (block and not self._closed and not self._ending
                   and not self._queue):
                self._cond.wait()
            if self._closed or self._ending or not self._queue:
                return None
            return self._queue.pop(0)

    def _requeue_front(self, entry: _QueuedPrompt) -> None:
        with self._cond:
            self._queue.insert(0, entry)

    @property
    def is_closed(self) -> bool:
        with self._cond:
            return self._closed

    @property
    def turn_in_flight(self) -> bool:
        with self._cond:
            return self._in_flight

    def live_state(self):
        """This run's status now, as a `ScreenState` — what the SDK runner's
        `live_state` answers for its tier, read here off the ACP update
        stream (`AcpActivity`)."""
        return self._activity.screen_state(session_id=self._session_id,
                                           cwd=self._cwd, model=self._model)

    def open_tool_use_ids(self) -> set[str]:
        return self._activity.open_tool_use_ids()

    def _on_update(self, session_id: str | None, update: dict) -> None:
        if session_id == self._session_id:
            self._activity.apply(update)

    # ── lifecycle ────────────────────────────────────────────────

    def stop(self) -> tuple[bool, str]:
        """Cancel the in-flight turn and tear the run down. The queue does
        not survive a stop — unlike `interrupt_run`, which cancels the turn
        alone and leaves the session steerable."""
        try:
            self._client.session_cancel(self._session_id)
        except Exception:  # noqa: BLE001 — stop is best-effort
            log.error("acp_cancel_failed", trace_id=self.trace_id,
                      exc_info=True)
            return False, "cancel failed"
        self.close("stopped")
        return True, "cancelled"

    def end_after_turn(self) -> tuple[bool, str]:
        """Close the run once the turn now running has finished; queued
        steers are dropped. An idle run closes at once — its pump is parked
        on the queue and wakes to close."""
        with self._cond:
            if self._closed:
                return False, "agent session is no longer running"
            if self._ending:
                return True, "already ending"
            self._ending = True
            self._queue.clear()
            self._cond.notify_all()
        return True, "ending after this turn"

    def close(self, reason: str = "closed") -> None:
        """Idempotent teardown: drop the queue, close the wire, unregister,
        then settle `failed` unless a turn already did. A non-one-shot run
        stays registered and steerable until this is called (or `stop()`
        is) — the ACP arm of `idle_timeout_sec = 0`."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
            self._cond.notify_all()
        self.permissions.cancel_all(close=True)
        acp_registry.unregister(self.trace_id)
        # Taken only once unregistered, so a callback added mid-close still
        # runs here rather than at once while the id reads as owned.
        with self._cond:
            self._torn_down = True
            callbacks, self._close_callbacks = self._close_callbacks, []
        try:
            self._client.close()
        except Exception:  # noqa: BLE001 — teardown must not raise
            log.error("acp_close_failed", trace_id=self.trace_id,
                      exc_info=True)
        _record_run_closed(self)
        _end_trace_session(self.trace_id, reason)
        self._settle(RunOutcome(self.trace_id, "failed",
                                f"channel closed: {reason}"))
        for callback in callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 — teardown must not raise
                log.error("acp_close_callback_failed", trace_id=self.trace_id,
                          exc_info=True)

    # Pump-side helpers, not part of the public surface.

    def _set_in_flight(self, value: bool) -> None:
        with self._cond:
            self._in_flight = value
        if value:
            self._activity.turn_started()
        else:
            self._activity.turn_ended()


def _spawn_client(env: dict | None, cwd: str | None,
                  cmd: list[str] | None, provider=None) -> AcpClient:
    client = AcpClient(cmd=cmd, env=env, cwd=cwd, provider=provider)
    try:
        client.connect()
    except AcpLaunchRefused as exc:
        raise LaunchRefused(str(exc)) from exc
    return client


def _accepts_images(init: dict) -> bool:
    caps = (init.get("agentCapabilities") or {}).get("promptCapabilities")
    return bool((caps or {}).get("image"))


def _start_session(client: AcpClient, init: dict, cwd: str,
                   resume: str | None) -> str:
    """A fresh session, or `resume` taken over under its own id — the trace
    keeps landing on the conversation it continues (Invariant 2)."""
    if not resume:
        return client.session_new(cwd)
    if not (init.get("agentCapabilities") or {}).get("loadSession"):
        raise LaunchRefused("the agent does not declare loadSession, so "
                            f"{resume} cannot be resumed over ACP")
    try:
        client.session_load(resume, cwd)
    except AcpError as exc:
        raise LaunchRefused(f"the agent could not load {resume}: {exc}") from exc
    return resume


def _open_session(client: AcpClient, cwd: str, model: str,
                  images: list | None, resume: str | None = None,
                  mode: str | None = "yolo") -> str:
    """Handshake + session/new (or session/load) + the unattended mode tier +
    the armed model.

    `mode` is the provider's unattended tier — kimi's yolo ("Ask When
    Needed"), codebuddy's bypassPermissions. Routine calls never reach the
    wire; what does parks for the operator (`permissions.PermissionParks`).
    An armed model or image
    the agent cannot take refuses the launch rather than running without it.
    """
    init = client.initialize()
    if images and not _accepts_images(init):
        raise LaunchRefused(
            f"the task has {len(images)} image(s) but the agent does not "
            "declare image prompts (promptCapabilities.image); remove the "
            "images or arm a provider that accepts them")
    session_id = _start_session(client, init, cwd, resume)
    try:
        if mode:
            client.session_set_mode(session_id, mode)
    except AcpError:
        log.error("acp_set_mode_failed", trace_id=session_id, mode=mode,
                  exc_info=True)
    if model:
        try:
            client.session_set_model(session_id, model)
        except AcpError as exc:
            raise LaunchRefused(
                f"the agent refused model {model!r}: {exc}") from exc
    return session_id


def _record_run_started(handle: AcpRunHandle, cwd: str,
                        model: str, provider_id: str) -> None:
    """The `agent_runs` row claiming this run while it lives, and the
    `sessions` row that makes it visible before any hook reports in.

    The pid is what keeps `reap_orphaned_runs` honest: a `running` row with
    a dead or missing pid is condemned, and `find_run` is how the steer
    routes tell "regin launched this" from "never ours". Imported here, not
    at module level: `agent_acp` must not import `agent_sdk` at import
    time, or the ownership seam the SDK registry routes through this
    package cycles. A failed write must not kill a run over bookkeeping.
    """
    try:
        from lib.agent_sdk import store as sdk_store

        sdk_store.upsert_run(handle.trace_id, status="running",
                             pid=handle._client.pid, cwd=cwd,
                             model=model or None)
        sdk_store.open_trace_session(handle.trace_id, agent_type=provider_id,
                                     cwd=cwd, model=model)
    except Exception:  # noqa: BLE001 — bookkeeping must not fail the launch
        log.error("acp_run_row_unwritten", trace_id=handle.trace_id,
                  exc_info=True)


def _record_run_closed(handle: AcpRunHandle) -> None:
    """Settle the `agent_runs` row. A run that got to turn at all reports
    its last turn's verdict; one that never did (closed in the launch
    window) says so."""
    detail = handle.turns[-1].detail if handle.turns else "closed"
    try:
        from lib.agent_sdk import store as sdk_store

        sdk_store.upsert_run(handle.trace_id, status="exited", detail=detail)
    except Exception:  # noqa: BLE001 — teardown must not raise
        log.error("acp_run_row_unsettled", trace_id=handle.trace_id,
                  exc_info=True)


def _end_trace_session(trace_id: str, reason: str) -> None:
    """Settle the `sessions` row of a run whose channel just closed.

    kimi emits no `session.end` over ACP, so without this the row keeps its
    `status='active'` until the next boot's `settle_ended_run_sessions`, and
    every status surface reads Working on a session nothing can steer.

    Skipped while the id is owned again: a resume of the same session can
    reserve and revive it between `unregister` and this write, and stamping
    that row ended would kill the new run's card.
    """
    try:
        from lib.agent_sdk import registry as sdk_registry
        from lib.agent_sdk import store as sdk_store

        if sdk_registry.is_sdk_owned(trace_id):
            return
        sdk_store.end_trace_session(trace_id, reason=reason)
    except Exception:  # noqa: BLE001 — teardown must not raise
        log.error("acp_trace_session_unended", trace_id=trace_id,
                  exc_info=True)


def launch_run(prompt: str, *, cwd: str | None = None,
               env: dict | None = None, model: str = "",
               permission_mode: str = "",
               one_shot: bool = False, images: list | None = None,
               client: AcpClient | None = None,
               cmd: list[str] | None = None,
               resume: str | None = None,
               provider=None) -> AcpRunHandle:
    """Open an ACP session and schedule the prompt turn on a worker thread.

    Registered before the turn thread starts, so `is_owned` is true for the
    whole live window; `one_shot` handles close themselves once the first
    turn ends and the queue drains, the rest live until `close()`/`stop()`/
    `end_after_turn()`. ACP requires an absolute `cwd`; an unset one is the
    project root.

    `resume` loads that session instead of opening a new one. A blank prompt
    runs no launch turn: the session comes up and waits for its first steer,
    as a bare `kimi` does at a terminal.

    The child's argv is `cmd`, else `provider.acp_argv()` (kimi by default).

    `permission_mode` is the operator's armed tier; an unarmed run falls back
    to the provider's own unattended one.
    """
    cwd = os.path.abspath(cwd or settings.project_root)
    own = client is None
    if client is None:
        client = _spawn_client(env, cwd, cmd, provider)
    try:
        mode = (permission_mode or getattr(provider, "acp_unattended_mode", None)
                or "yolo")
        session_id = _open_session(client, cwd, model, images, resume, mode)
    except Exception:
        if own:
            client.close()
        raise
    future: Future = Future()
    handle = AcpRunHandle(session_id, future, client, session_id,
                          cwd=cwd, model=model)
    client.on_update(handle._on_update)
    client.on_request("session/request_permission", handle.permissions.ask,
                      blocking=True)
    acp_registry.register(handle)
    _record_run_started(handle, cwd, model,
                        getattr(provider, "provider_id", None) or "kimi")
    threading.Thread(target=_pump, daemon=True,
                     name=f"acp-run-{session_id}",
                     args=(handle, prompt, one_shot, images)).start()
    return handle


def _run_turn(client: AcpClient, session_id: str, text: str,
              images: list | None = None) -> RunOutcome:
    """One session/prompt round trip, as its own turn on the shared session.
    `end_turn` is the one clean stop; any other stopReason (cancelled,
    max_tokens, refusal…) is a turn that did not finish on its own terms."""
    result = client.session_prompt(session_id, text, images=images)
    reason = result.stop_reason or END_TURN
    status = "exited" if reason == END_TURN else "failed"
    return RunOutcome(session_id, status, reason)


def _pump(handle: AcpRunHandle, prompt: str, one_shot: bool,
          images: list | None = None) -> None:
    """The turn thread: the launch prompt, then the steer queue, serially.

    One prompt on the wire at a time — each is awaited to its stopReason
    before the next is sent, which is the only thing standing between this
    queue and the server's turn.agent_busy rejection. A queued prompt that
    is rejected anyway (a cancel or a slow turn-close racing the send) goes
    back to the head of the queue and is retried after a growing backoff,
    never dropped and never spun. A turn that fails any other way means the
    wire is dead — nothing further can run, so the handle closes. One-shot
    handles close once the queue drains; the others wait on the condition
    for the next steer, `end_after_turn()` or `close()`.

    A turn that stops other than `end_turn` settles the handle `failed` but
    leaves the session open: an interrupt is exactly such a turn, and the
    session must stay steerable after it.
    """
    client, session_id = handle._client, handle._session_id
    backoff = _BUSY_RETRY_SEC
    launch_pending = bool(prompt.strip())
    while True:
        if launch_pending:
            entry, text, turn_images = None, prompt, images
        else:
            entry = handle._take(block=not one_shot)
            if entry is None:
                handle.close("one-shot turn ended" if one_shot
                             else "ended after the turn")
                return
            text, turn_images = entry.text, None
        handle._set_in_flight(True)
        dead = False
        try:
            outcome = _run_turn(client, session_id, text, turn_images)
        except AcpTurnBusy:
            if entry is not None:
                handle._requeue_front(entry)
                time.sleep(backoff)
                backoff = min(backoff * 2, _BUSY_RETRY_MAX_SEC)
                continue
            # The launch prompt never ran: nothing is left to steer.
            outcome = RunOutcome(session_id, "failed", "turn.agent_busy")
            dead = True
        except Exception as exc:  # noqa: BLE001 — wire death is a failed turn
            log.error("acp_turn_failed", trace_id=session_id, exc_info=True)
            outcome, dead = RunOutcome(session_id, "failed", str(exc)), True
        finally:
            handle._set_in_flight(False)
        backoff = _BUSY_RETRY_SEC
        launch_pending = False
        if dead and handle.is_closed:
            # A close killed the wire under this turn; its own reason
            # ("stopped") is the true one, and it settles with it.
            return
        handle.turns.append(outcome)
        if outcome.status != "exited":
            handle._settle(outcome)
        if dead:
            handle.close(outcome.detail)
            return
