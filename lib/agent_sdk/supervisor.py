"""Run agent sessions inside the web process.

The parked-question registry is process-local, so answering from `/live` only
works if the runner lives in the same process as the Flask route that resolves
it. This module owns one long-lived asyncio loop on a daemon thread and
schedules runs onto it — that shared loop is what makes the typed answer path
reachable from a browser at all.

The loop is created lazily: a regin install with `agent_sdk` off never starts a
thread, and importing this module costs nothing.

`launch` serves an operator's session. `launch_run` serves regin's own spawns:
it carries the environment and per-run model/permission mode the caller's job
needs, can end the session with its first turn, and hands back a `RunHandle` —
the completion signal a programmatic caller has to have and a watching UI does
not.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass

from lib.activity_log import get_activity_logger
from lib.settings import settings
from . import client, registry, store
from .runner import run_session

log = get_activity_logger("agent_sdk")

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None


class LaunchRefused(RuntimeError):
    """Structured refusal — disabled, at capacity, or missing the SDK."""


def _serve(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _lock:
        if _loop is not None and not _loop.is_closed():
            return _loop
        _loop = asyncio.new_event_loop()
        threading.Thread(target=_serve, args=(_loop,), daemon=True,
                         name="agent-sdk-loop").start()
        return _loop


def _on_done(trace_id: str, token: object, future) -> None:
    """Give the trace id back, and drain the run's result so a failure is
    logged rather than swallowed by the loop's default exception handler.

    Runs on every terminal path — returned, raised and cancelled — which is
    what the reservation needs: one that outlived its run would refuse every
    later resume of that id for the life of the process.
    """
    registry.release_run(trace_id, token)
    error = future.exception() if not future.cancelled() else None
    if error is not None:
        log.error("sdk_run_crashed", trace_id=trace_id, detail=repr(error))


@dataclass(frozen=True)
class RunOutcome:
    """How a run ended, in the same vocabulary as the `agent_runs` row."""

    trace_id: str
    status: str
    detail: str = ""


class RunHandle:
    """The completion signal for a launched run.

    `launch` returns before the agent has done anything, which is the point —
    but a programmatic caller (unlike `/live`, which watches the trace) needs
    to know when the run ended and how. That signal is the
    `concurrent.futures.Future` the loop already hands back, wrapped here:
    it gives a callback (`add_done_callback`) *and* a bounded block (`wait`)
    from the one mechanism, so a Flask thread can stay non-blocking while a
    worker thread that genuinely wants the result doesn't need a second one.
    Polling `GET /api/agent-runs/<id>` remains the answer for a reader in
    another process — this is the in-process, no-poll path.

    The outcome itself is read back from the durable `agent_runs` row rather
    than the coroutine's return value: the runner writes that row in a
    `finally`, so it is the one report that exists on every exit path.
    """

    def __init__(self, trace_id: str, future):
        self.trace_id = trace_id
        self._future = future

    def done(self) -> bool:
        return self._future.done()

    def wait(self, timeout: float | None = None) -> RunOutcome:
        """Block up to `timeout` seconds for the run to end.

        Raises `TimeoutError` if it hasn't — the run keeps going, and `stop`
        is how a caller that has given up ends it.
        """
        try:
            self._future.result(timeout)
        except TimeoutError:
            # An Exception subclass, so it would otherwise be reported below
            # as a run that failed — the opposite of a run still going.
            raise
        except Exception as exc:
            return RunOutcome(self.trace_id, "failed", str(exc))
        return self._stored_outcome()

    def add_done_callback(self, callback) -> None:
        """Call `callback(RunOutcome)` when the run ends, on the shared loop's
        thread — so it must not block."""
        self._future.add_done_callback(
            lambda f: callback(self._outcome(f)))

    def stop(self) -> tuple[bool, str]:
        return registry.stop_run(self.trace_id)

    def _outcome(self, future) -> RunOutcome:
        error = future.exception() if not future.cancelled() else None
        if error is not None:
            return RunOutcome(self.trace_id, "failed", str(error))
        return self._stored_outcome()

    def _stored_outcome(self) -> RunOutcome:
        row = store.get_run(self.trace_id) or {}
        return RunOutcome(self.trace_id, row.get("status") or "exited",
                          row.get("detail") or "")


def _refuse_unless_launchable(prompt: str, resume: str | None) -> None:
    """A run with nothing to do is refused — unless it is a resume, where
    reopening the session is the whole act and the first turn can arrive later
    through `send_prompt`."""
    if not settings.agent_sdk.enabled:
        raise LaunchRefused("agent_sdk disabled")
    if not (prompt or "").strip() and not resume:
        raise LaunchRefused("prompt required")
    if registry.active_run_count() >= settings.agent_sdk.max_concurrent_runs:
        raise LaunchRefused("max_concurrent_runs reached")


def launch_run(prompt: str, *, cwd: str | None = None,
               env: dict[str, str] | None = None,
               permission_mode: str = "", model: str = "",
               one_shot: bool = False,
               resume: str | None = None,
               trace_id: str | None = None) -> RunHandle:
    """Schedule a run on the shared loop and return its completion handle.

    This is the programmatic entry point: `env` reaches the launched agent's
    process (as an overlay on this one's), and `permission_mode` / `model`
    override the global defaults for this run alone. `one_shot` ends the
    session with its first turn instead of leaving it open for follow-ups.
    `resume` continues an earlier session instead of starting a fresh one.

    `trace_id` reuses an earlier run's identity rather than minting one, which
    is what makes resuming a stopped run *the same session* to every reader:
    the row is revived, the spans keep landing on one trace, and the child
    keeps its own session id, so the pair stays aliased. An id this process is
    already running — or already starting — under is refused rather than fused
    into the run holding it.
    """
    _refuse_unless_launchable(prompt, resume)
    trace_id = trace_id or f"sdk-{uuid.uuid4().hex[:12]}"
    # Claimed before the run is scheduled, because ownership has to be true
    # before the child exists: the runner registers only after `connect()` has
    # spawned it, and two launches reusing one trace id inside that window
    # would both be admitted — the second runner replacing the first, whose
    # teardown then evicts it, orphaning a live child.
    token = object()
    if not registry.reserve_run(trace_id, token):
        raise LaunchRefused("that run is already starting")
    try:
        loop = _ensure_loop()
        options = client.RunOptions(env=dict(env or {}),
                                    permission_mode=permission_mode,
                                    model=model)
        future = asyncio.run_coroutine_threadsafe(
            run_session(trace_id, prompt, cwd=cwd, options=options,
                        one_shot=one_shot, resume=resume),
            loop)
    except BaseException:
        registry.release_run(trace_id, token)
        raise
    future.add_done_callback(lambda f: _on_done(trace_id, token, f))
    log.write("sdk_run_launched", trace_id=trace_id, cwd=cwd,
              one_shot=one_shot, resumed_from=resume)
    return RunHandle(trace_id, future)


def launch(prompt: str, *, cwd: str | None = None,
           model: str = "", permission_mode: str = "",
           one_shot: bool = False, resume: str | None = None,
           trace_id: str | None = None) -> str:
    """Start a session for `prompt` and return its trace id immediately.

    Returns as soon as the run is scheduled — the agent works on the shared
    loop while the caller's request completes, which is what lets `/live` show
    the session and answer its questions while it runs. The run does not end
    with the prompt: it stays open for follow-ups (`send_prompt`) until it is
    stopped or goes idle, unless `one_shot`.

    The operator's launch path, so the per-run overrides an operator can pick
    on `/live` travel here rather than only through `launch_run`.
    """
    return launch_run(prompt, cwd=cwd, model=model,
                      permission_mode=permission_mode, one_shot=one_shot,
                      resume=resume, trace_id=trace_id).trace_id


def send_prompt(trace_id: str, text: str) -> tuple[bool, str]:
    """Queue a follow-up prompt onto a run this process owns.

    Reachability is the registry's answer, not the stored status: a `running`
    row left behind by a previous process names a session nobody can talk to.
    """
    if not (text or "").strip():
        return False, "prompt required"
    return registry.submit_prompt(trace_id, text)
