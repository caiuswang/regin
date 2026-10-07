"""Launch a task run through a provider's ACP channel (feature #140).

`launch_run` mirrors `lib.agent_sdk.supervisor.launch_run`'s contract: the
call returns a handle before the turn runs, and the handle settles a
`RunOutcome` when the turn ends. Unlike the SDK arm there is no trace-id
placeholder or alias group — `session/new` answers with the session id the
child will be traced under before the prompt is even sent, so the handle's
`trace_id` is the real one from birth.

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
import shutil
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import NamedTuple

from lib.activity_log import get_activity_logger

from . import registry as acp_registry
from .client import (AcpClient, AcpError, AcpLaunchRefused, AcpTurnBusy)

log = get_activity_logger("agent_acp")

# Pacing for a queued prompt the server rejects as turn.agent_busy anyway
# (a cancel or a slow turn-close can race the next send). The retry doubles
# up to the cap so a server that stays busy is retried, never spun.
_BUSY_RETRY_SEC = 0.25
_BUSY_RETRY_MAX_SEC = 4.0


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
                 client: AcpClient, session_id: str) -> None:
        self.trace_id = trace_id
        self._future = future
        self._client = client
        self._session_id = session_id
        # Every turn that ran, launch turn first. Written by the pump thread
        # only; readers poll it from other threads (a list append is atomic
        # under the GIL, and each outcome is a frozen dataclass).
        self.turns: list[RunOutcome] = []
        self._cond = threading.Condition()
        self._queue: list[_QueuedPrompt] = []
        self._prompt_ids = itertools.count(1)
        self._closed = False
        self._in_flight = False

    # ── completion (unchanged contract: settles with the LAUNCH turn) ──

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

    def _settle_launch(self, outcome: RunOutcome) -> None:
        """Settle the future with the launch turn's outcome. Task attempts
        settle promptly off this; later turns are observable in `turns`
        only, and must never move a settled attempt."""
        if not self._future.done():
            self._future.set_result(outcome)

    # ── steer queue ──────────────────────────────────────────────

    def enqueue(self, text: str) -> bool:
        """Queue a follow-up prompt behind the current turn. False once the
        handle is closed — the prompt has nowhere to run."""
        with self._cond:
            if self._closed:
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
        """Pop the next queued steer. None when closed, or — non-blocking —
        when the queue is empty. `close()` wakes a blocked pump."""
        with self._cond:
            while block and not self._closed and not self._queue:
                self._cond.wait()
            if self._closed or not self._queue:
                return None
            return self._queue.pop(0)

    def _requeue_front(self, entry: _QueuedPrompt) -> None:
        with self._cond:
            self._queue.insert(0, entry)

    @property
    def turn_in_flight(self) -> bool:
        with self._cond:
            return self._in_flight

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
        self.close()
        return True, "cancelled"

    def close(self) -> None:
        """Idempotent teardown: drop the queue, close the wire, unregister.
        A non-one-shot run stays registered and steerable until this is
        called (or `stop()` is) — the ACP arm of `idle_timeout_sec = 0`."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
            self._cond.notify_all()
        acp_registry.unregister(self.trace_id)
        try:
            self._client.close()
        except Exception:  # noqa: BLE001 — teardown must not raise
            log.error("acp_close_failed", trace_id=self.trace_id,
                      exc_info=True)
        _record_run_closed(self)

    # Pump-side helpers, not part of the public surface.

    def _set_in_flight(self, value: bool) -> None:
        with self._cond:
            self._in_flight = value


def _spawn_client(env: dict | None, cwd: str | None,
                  cmd: list[str] | None) -> AcpClient:
    if not cmd and not shutil.which("kimi"):
        raise LaunchRefused("kimi CLI not found on PATH")
    client = AcpClient(cmd=cmd, env=env, cwd=cwd)
    try:
        client.connect()
    except AcpLaunchRefused as exc:
        raise LaunchRefused(str(exc)) from exc
    return client


def _open_session(client: AcpClient, cwd: str | None,
                  model: str) -> str:
    """Handshake + session/new + the unattended mode tier.

    yolo ("Ask When Needed") is the phase-1 equivalent of an operator-armed
    run: routine calls never reach the wire, and what does is answered with
    the cancellation policy the client registers.
    """
    client.initialize()
    session_id = client.session_new(cwd or ".")
    try:
        client.session_set_mode(session_id, "yolo")
    except AcpError:
        log.error("acp_set_mode_failed", trace_id=session_id, exc_info=True)
    if model:
        raise LaunchRefused(
            "model selection on the ACP channel is not wired yet")
    return session_id


def _record_run_started(handle: AcpRunHandle) -> None:
    """The `agent_runs` row claiming this run while it lives.

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
                             pid=handle._client.pid)
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


def launch_run(prompt: str, *, cwd: str | None = None,
               env: dict | None = None, model: str = "",
               one_shot: bool = False, images: list | None = None,
               client: AcpClient | None = None,
               cmd: list[str] | None = None) -> AcpRunHandle:
    """Open an ACP session and schedule the prompt turn on a worker thread.

    Registered before the turn thread starts, so `is_owned` is true for the
    whole live window; `one_shot` handles close themselves once the queue
    drains, the rest live until `close()`/`stop()`.
    """
    if images:
        raise LaunchRefused("launch images are not supported on the ACP "
                            "channel yet")
    own = client is None
    if client is None:
        client = _spawn_client(env, cwd, cmd)
    try:
        session_id = _open_session(client, cwd, model)
    except Exception:
        if own:
            client.close()
        raise
    future: Future = Future()
    handle = AcpRunHandle(session_id, future, client, session_id)
    acp_registry.register(handle)
    _record_run_started(handle)
    threading.Thread(target=_pump, daemon=True,
                     name=f"acp-run-{session_id}",
                     args=(handle, prompt, one_shot)).start()
    return handle


def _run_turn(client: AcpClient, session_id: str, text: str) -> RunOutcome:
    """One session/prompt round trip, as its own turn on the shared session."""
    result = client.session_prompt(session_id, text)
    return RunOutcome(session_id, "exited", result.stop_reason or "end_turn")


def _pump(handle: AcpRunHandle, prompt: str, one_shot: bool) -> None:
    """The turn thread: the launch prompt, then the steer queue, serially.

    One prompt on the wire at a time — each is awaited to its stopReason
    before the next is sent, which is the only thing standing between this
    queue and the server's turn.agent_busy rejection. A queued prompt that
    is rejected anyway (a cancel or a slow turn-close racing the send) goes
    back to the head of the queue and is retried after a growing backoff,
    never dropped and never spun. A turn that fails any other way means the
    wire is dead — nothing further can run, so the handle closes. One-shot
    handles close once the queue drains; the others wait on the condition
    for the next steer or `close()`.
    """
    client, session_id = handle._client, handle._session_id
    backoff = _BUSY_RETRY_SEC
    launch_pending = True
    while True:
        if launch_pending:
            entry, text = None, prompt
        else:
            entry = handle._take(block=not one_shot)
            if entry is None:
                if one_shot:
                    handle.close()
                return
            text = entry.text
        handle._set_in_flight(True)
        try:
            outcome = _run_turn(client, session_id, text)
        except AcpTurnBusy:
            if entry is not None:
                handle._requeue_front(entry)
                time.sleep(backoff)
                backoff = min(backoff * 2, _BUSY_RETRY_MAX_SEC)
                continue
            outcome = RunOutcome(session_id, "failed", "turn.agent_busy")
        except Exception as exc:  # noqa: BLE001 — wire death is a failed turn
            log.error("acp_turn_failed", trace_id=session_id, exc_info=True)
            outcome = RunOutcome(session_id, "failed", str(exc))
        finally:
            handle._set_in_flight(False)
        backoff = _BUSY_RETRY_SEC
        handle.turns.append(outcome)
        if launch_pending:
            launch_pending = False
            handle._settle_launch(outcome)
        if outcome.status != "exited":
            handle.close()
            return
