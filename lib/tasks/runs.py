"""The join between a task row and a run — `agent_task_runs`.

`lib/agent_sdk` needs no new engine: `supervisor.launch_run` launches,
`registry.stop_run` stops, `registry.interrupt_run` interrupts. Everything here
is bookkeeping around those calls, and every control path delegates rather than
reimplementing — there is no second stop, no second liveness probe, no second
queue.

Three rules govern the bookkeeping, and they are the same three
`lib/orm/models/agent_runs.py` states for its own rows:

  * the terminal write happens on the run's own completion path, in a
    `finally`, and is idempotent — a `running` row outlives the process that
    wrote it and misleads every later reader;
  * a reader merges the row's claim with liveness from `agent_runs` rather than
    trusting `status == 'running'`;
  * a row is never condemned on pid inequality alone — the recorded pid is
    probed (`sdk_store.run_is_live`), or a foreign process reaps a live peer's
    runs.

Firing is event-driven for the same reason: the completion that could change
which dependents are runnable is the thing that recomputes them, so there is no
scheduler and no interval.

Two events can be that completion, and they answer different questions. The run
ending (`handle_completion`) answers "the session is over"; the agent's own
`task finish` signal (`finish_run`) answers "the work is done". Only the second
one arrives while a steerable session is still open, and a session regin did
not launch `one_shot` never ends on its own — so the signal is the path that
carries a dependency chain, and the callback is its fallback. Both settle
through `complete_run`, which settles once, so neither can double-fire.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.agent_sdk import store as sdk_store, supervisor, worktree
from lib.orm import SessionLocal
from lib.orm.models.agent_runs import AgentRun
from lib.orm.models.tasks import (
    AgentTask, AgentTaskRun, RUN_STATUS_ARMED, RUN_STATUS_DONE,
    RUN_STATUS_FAILED, RUN_STATUS_RUNNING, RUN_STATUS_STARTING,
    TASK_STATUS_ARMED, TASK_STATUS_DONE, TASK_STATUS_FAILED,
    TASK_KIND_SPEC,
    TASK_STATUS_RUNNING,
)
from lib.settings import settings
from lib.tasks import deps, run_ending, store, tmux_runs
from lib.tasks.run_announce import (
    TASK_READY_TRACE, _announce_broken_chains, _announce_stuck)
from lib.tasks.run_prompt import (
    FOLLOWUP_MAX, PROMPT_MAX, _armed_base, _cli_command,
    _close_defect_command, _defects_named, _feature_block, _finish_block,
    _finish_command, _followup_block, _launch_images, _launch_kwargs,
    _open_fix_defects, _prompt, _tracker_block)
from lib.tasks.run_reports import (
    SETTLED_BY_AGENT, SETTLED_BY_SESSION, _post_agent_report,
    reopen_for_agent_report)

log = get_activity_logger("tasks")

_REASON_MAX = 2000

# The run's own vocabulary for how it ended (`RunOutcome.status`, read back
# from the durable `agent_runs` row) mapped onto the task's.
_TASK_STATUS_FOR = {RUN_STATUS_DONE: TASK_STATUS_DONE,
                    RUN_STATUS_FAILED: TASK_STATUS_FAILED}

_SETTLED = (TASK_STATUS_DONE, TASK_STATUS_FAILED)

# Bookkeeping regin keeps on an attempt's params, never shown or re-spent:
# a pending attempt released by a process that may not launch it.
_DEFERRED = "fire_deferred"
_INTERNAL_PARAMS = (_DEFERRED, run_ending.END_REQUESTED)


def _public_params(params: dict) -> dict:
    return {k: v for k, v in params.items() if k not in _INTERNAL_PARAMS}


class RunRefused(RuntimeError):
    """A run this task cannot start right now — a 409, never a crash.

    `blocking` carries the dependency ids at fault so the caller can name them
    instead of making the operator go find them.
    """

    def __init__(self, message: str, blocking=None,
                 field: Optional[str] = None) -> None:
        super().__init__(message)
        self.blocking = list(blocking or [])
        self.field = field


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _already_live(task_id: int) -> RunRefused:
    """The one refusal for "this task is already going".

    Built in one place because two paths raise it — the liveness check and the
    unique-constraint loser — and a caller must not be able to tell which one
    answered: they are the same fact observed a few microseconds apart.
    """
    return RunRefused(f"task {task_id} already has a live run")


def serialize_attempt(row: AgentTaskRun) -> dict:
    """One attempt as every surface renders it.

    Public because the task feed carries the pending one per row: the arm
    sheet seeds its draft from what the task was last armed with, and that
    now lives here rather than on the task.
    """
    return _serialize(row)


def _serialize(row: AgentTaskRun) -> dict:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "trace_id": row.trace_id,
        "attempt": row.attempt,
        "status": row.status,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "exit_reason": row.exit_reason,
        # The parameters THIS attempt spends. On the attempt rather than on
        # the task, so a rerun under different ones is a second row here and
        # not a second task — which is what retired "a sub-task is a rerun".
        "params": _public_params(store._decode_params(row.params_json)),
        "autorun": bool(row.autorun),
    }


def arm(task_id: int, *, params: dict, autorun: int) -> dict:
    """Write the parameters the task's next attempt will spend.

    An `armed` run row IS the pending attempt: arming a task used to set three
    columns on it and leave `status='armed'` standing for state nothing else
    could see, and the row makes that state a thing you can read, list and
    replace.

    Re-arming replaces the pending row rather than adding a second — there is
    one next attempt, and two would make "which parameters does Run spend"
    a question with two answers.
    """
    with SessionLocal() as session:
        row = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id,
                   AgentTaskRun.status == RUN_STATUS_ARMED)).first()
        if row is None:
            row = AgentTaskRun(task_id=task_id,
                               attempt=_next_attempt(session, task_id),
                               status=RUN_STATUS_ARMED)
        row.params_json = json.dumps(params)
        row.autorun = autorun
        row.started_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        return _serialize(row)


def armed_attempt(task_id: int) -> Optional[dict]:
    """The pending attempt, or None for a task nobody has armed."""
    with SessionLocal() as session:
        row = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id,
                   AgentTaskRun.status == RUN_STATUS_ARMED)).first()
        return _serialize(row) if row is not None else None


def drop_armed(task_id: int) -> Optional[int]:
    """Delete the pending attempt, returning its id; None when there was none.

    Only the `armed` row goes: it never started, so no history is lost, while
    every launched attempt stays for the run history.
    """
    with SessionLocal() as session:
        row = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id,
                   AgentTaskRun.status == RUN_STATUS_ARMED)).first()
        if row is None:
            return None
        run_id = row.id
        session.delete(row)
        session.commit()
    return run_id


def open_attempt(task_id: int, *, params: Optional[dict] = None,
                 autorun: int = 0) -> dict:
    """An attempt row, written *before* the launch: a crash between the two
    leaves this visible `starting` row rather than an untracked child.

    `UNIQUE(task_id, attempt)` closes the window between the liveness check and
    this insert. Two requests that both read "not live" compute the same next
    attempt, so the constraint picks the winner and the loser is refused with
    the liveness check's own words — identity resolving a race, exactly as it
    resolves a redelivered capture, and never a lock or a retry.
    """
    with SessionLocal() as session:
        row = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id,
                   AgentTaskRun.status == RUN_STATUS_ARMED)).first()
        if row is None:
            # A rerun with no new parameters spends the previous attempt's:
            # "Run again" is the same attempt made twice, and re-arming is how
            # an operator asks for different ones.
            previous = session.exec(
                select(AgentTaskRun).where(AgentTaskRun.task_id == task_id)
                .order_by(AgentTaskRun.attempt.desc())).first()
            row = AgentTaskRun(
                task_id=task_id, attempt=_next_attempt(session, task_id),
                params_json=(json.dumps(params) if params is not None
                             else getattr(previous, "params_json", None)),
                autorun=(autorun if params is not None
                         else int(getattr(previous, "autorun", 0) or 0)))
        elif params is not None:
            row.params_json = json.dumps(params)
            row.autorun = autorun
        if row.params_json:
            row.params_json = json.dumps(
                _public_params(store._decode_params(row.params_json)))
        row.status = RUN_STATUS_STARTING
        row.started_at = _now()
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            log.warn("task_run_race_lost", task_id=task_id)
            raise _already_live(task_id) from None
        session.refresh(row)
        return _serialize(row)


# ── liveness ─────────────────────────────────────────────────

def _trace_is_live(trace_id: str) -> bool:
    """Whether the run behind `trace_id` is still reachable.

    The registry answers first because it is the strongest evidence there is —
    a runner in this process — and the durable row answers for a run another
    process owns, by probing its pid.
    """
    from lib import agent_sdk

    if agent_sdk.is_sdk_owned(trace_id) or agent_sdk.is_starting(trace_id):
        return True
    return sdk_store.run_is_live(trace_id)


def is_live(view: dict) -> bool:
    """Whether the agent behind an attempt is still reachable.

    A probe of the run, never a reading of the row. `finish_run` settles an
    attempt while its session deliberately keeps going, so `ended_at` and a
    terminal `status` stopped implying the agent is gone — and asking the row
    would make the run history report `live: false` for the very run a second
    launch is refused for.

    An attempt with no `trace_id` is not live: it was written before the launch
    that would have minted one, so there is no process to probe — which is
    exactly the visible `starting` row a crash between the two is meant to
    leave, rather than a task blocked forever by a run that never existed.
    """
    if not view["trace_id"]:
        return False
    if tmux_runs.is_tmux(view.get("params")):
        # UNKNOWN counts as live: a run tmux cannot vouch for either way is
        # not one to launch a second agent into the same worktree beside —
        # unless the operator already stopped it, when only tmux positively
        # seeing the session keeps the task from being run again.
        seen = tmux_runs.liveness(view)
        if view.get("exit_reason") == tmux_runs.STOPPED_REASON:
            return seen == tmux_runs.LIVE
        return seen != tmux_runs.GONE
    return _trace_is_live(view["trace_id"])


# ── reads ────────────────────────────────────────────────────

def latest_attempt(task_id: int) -> Optional[dict]:
    """The newest attempt that has actually been launched.

    The pending `armed` row is excluded, and that exclusion is load-bearing:
    `live_run` reads this, so an armed row answering here would report "not
    live" for a task whose previous run is still going and let a second launch
    into the same worktree. `armed_attempt` is how the pending one is read.
    """
    with SessionLocal() as session:
        row = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id,
                   AgentTaskRun.status != RUN_STATUS_ARMED)
            .order_by(AgentTaskRun.attempt.desc())).first()
        return _serialize(row) if row is not None else None


def live_run(task_id: int) -> Optional[dict]:
    """The task's attempt whose agent is still reachable, or None. Never
    `status` alone.

    The LATEST attempt, settled or not. `finish_run` settles an attempt while
    deliberately leaving its session running, so `ended_at` stopped implying
    the agent is gone — and skipping settled rows would admit a second launch
    into the same worktree while the first agent is still working, with only
    the newer one reachable by Stop.

    The tail of that trade: a task run is launched in THIS process, and
    `sdk_store.run_is_live` spares its own pid without probing it. A runner
    killed hard enough to skip its teardown therefore leaves a row that answers
    "live" until the server restarts, and the task cannot be re-run until then.
    Stop clears it, and so does the boot reaper.
    """
    view = latest_attempt(task_id)
    return view if view is not None and is_live(view) else None


def _agent_runs(session, trace_ids: list[str]) -> dict[str, dict]:
    if not trace_ids:
        return {}
    rows = session.exec(
        select(AgentRun).where(AgentRun.trace_id.in_(trace_ids))).all()
    return {r.trace_id: {"status": r.status, "pid": r.pid, "cwd": r.cwd,
                         "model": r.model, "detail": r.detail,
                         "cli_session_id": r.cli_session_id} for r in rows}


def list_runs(task_id: int) -> list[dict]:
    """Attempt history, newest first, joined to `agent_runs`.

    The join is what makes `live` answerable: the attempt row records intent,
    and the `agent_runs` row plus its pid records reachability.

    History, so the pending `armed` row is not in it: nothing has happened to
    that attempt yet. The detail envelope carries it separately.
    """
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id,
                   AgentTaskRun.status != RUN_STATUS_ARMED)
            .order_by(AgentTaskRun.attempt.desc())).all()
        views = [_serialize(row) for row in rows]
        agents = _agent_runs(session, [v["trace_id"] for v in views
                                       if v["trace_id"]])
    for view in views:
        view["agent_run"] = agents.get(view["trace_id"])
        view["live"] = is_live(view)
    log.read("task_runs_read", task_id=task_id, count=len(views))
    return views


# ── launching ────────────────────────────────────────────────


def _refuse_unrunnable(task: dict) -> None:
    """Why this task cannot start, in the order the operator needs to hear it.

    The live-run check comes first because it explains the task's own status: a
    task that is `running` is not "not armed", it is already going.
    """
    task_id = task["id"]
    if live_run(task_id) is not None:
        raise _already_live(task_id)
    blocked = deps.blocking(task_id)
    if blocked:
        raise RunRefused(
            "blocked by " + ", ".join(str(dep) for dep in blocked),
            blocking=blocked)
    # `running` passes: the check above already proved nothing backs that
    # claim. A runner killed with its server leaves the status behind, and
    # refusing on the row alone would strand the task forever.
    if task["status"] in (TASK_STATUS_ARMED, TASK_STATUS_RUNNING):
        return
    # A settled task keeps the parameters it was armed with, and Run spends
    # them again: a feature that failed once, or shipped and needs another
    # round, is not "not armed". A task never armed still is. The parameters
    # live on the attempts now, so "was it armed" is "is there one to reuse".
    if task["status"] in _SETTLED and latest_attempt(task_id) is not None:
        return
    raise RunRefused(
        f"task {task_id} is {task['status']!r}, not 'armed' — arm it first")


def _next_attempt(session, task_id: int) -> int:
    last = session.exec(
        select(AgentTaskRun.attempt)
        .where(AgentTaskRun.task_id == task_id)
        .order_by(AgentTaskRun.attempt.desc())).first() or 0
    return last + 1


def _attach_trace(run_id: int, trace_id: str) -> dict:
    with SessionLocal() as session:
        row = session.get(AgentTaskRun, run_id)
        row.trace_id = trace_id
        row.status = RUN_STATUS_RUNNING
        session.add(row)
        session.commit()
        session.refresh(row)
        return _serialize(row)


def _resolve_stuck(task_id: int) -> None:
    """Retire the card a previous refusal left.

    `_announce_stuck` emits `once`, so a live card suppresses the next one: a
    task that stuck for a full queue and later sticks on a broken worktree
    would report the first reason forever.
    """
    from lib.agent_messages import events

    events.resolve(TASK_READY_TRACE, f"task-ready:{task_id}")
    # The chain-stopped card is keyed on the slice that broke, and emitted
    # `once` for the same reason: re-arming and running that slice is the act
    # that unbreaks the chain, so its card must retire here or a second break
    # of the same slice would never be announced.
    events.resolve(TASK_READY_TRACE, f"chain-stopped:{task_id}")


def start_run(task_id: int) -> Optional[dict]:
    """Launch one attempt at a task. None when the task is unknown.

    Raises `RunRefused` for a task that is blocked, unarmed or already running,
    or whose launch was refused — the last after settling the attempt failed.
    """
    task = store.get_task(task_id)
    if task is None:
        return None
    _refuse_unrunnable(task)
    row = open_attempt(task_id)
    try:
        kwargs = _launch_kwargs(row["params"])
    except (worktree.WorktreeRefused, OSError,
            subprocess.SubprocessError) as exc:
        raise _refuse_launch(row, exc) from exc
    if tmux_runs.is_tmux(row["params"]):
        return _start_tmux(task, row, kwargs)
    try:
        runner = _channel_launcher((row["params"] or {}).get("provider")
                                   or "claude")
    except RunRefused as exc:
        raise _refuse_launch(row, exc) from exc
    if runner is not None:
        return _start_channel(task, row, runner, kwargs)
    try:
        handle = supervisor.launch_run(_prompt(task, row["id"],
                                               row["params"]),
                                       images=_launch_images(task),
                                       **kwargs)
    except (supervisor.LaunchRefused, ImportError) as exc:
        raise _refuse_launch(row, exc) from exc
    view = _mark_started(task, row, handle.trace_id)
    # Registered last: a run that ends before this point fires the callback the
    # moment it is added, and it must not race the writes above.
    handle.add_done_callback(
        lambda outcome: handle_completion(task_id, row["id"], outcome))
    log.write("task_run_started", task_id=task_id, run_id=row["id"],
              trace_id=handle.trace_id, attempt=view["attempt"])
    return view


def _channel_launcher(provider_id: str):
    """The provider's structured-channel launcher, or None for Claude (the
    sdk arm below). Feature #140's seam: adding a channel-launched
    provider is an adapter override, not a branch here. Any other provider
    without a channel is refused, never launched as Claude."""
    if provider_id == "claude":
        return None
    from lib.providers.registry import build_provider  # noqa: PLC0415
    runner = build_provider(provider_id).channel_launcher()
    if runner is None:
        raise RunRefused(f"{provider_id} has no task launch channel")
    return runner


def _start_channel(task: dict, row: dict, runner, kwargs: dict) -> dict:
    """The structured-channel arm of `start_run` (kimi via ACP today).

    Unlike the SDK arm there is no trace-id placeholder: the channel answers
    with the child session id before the prompt is sent, so the attempt is
    marked running under the id the trace will actually land on.

    The channel's completion only ever reports a failure (D6): a turn ending
    `end_turn` leaves the attempt running for the agent's `task finish`.
    Unlike the SDK arm, `one_shot` is handed to the launch: the channel
    closes after the first turn, and an unreported attempt settles failed.
    """
    launch_kwargs = {name: kwargs.get(name)
                     for name in ("cwd", "env", "model", "permission_mode")}
    try:
        _prepare_workspace(row, launch_kwargs["cwd"])
        handle = runner(_prompt(task, row["id"], row["params"]),
                        one_shot=bool((row["params"] or {}).get("one_shot")),
                        images=_launch_images(task), **launch_kwargs)
    except Exception as exc:
        raise _refuse_launch(row, exc) from exc
    view = _mark_started(task, row, handle.trace_id)
    handle.add_done_callback(
        lambda outcome: handle_completion(task["id"], row["id"], outcome))
    log.write("task_run_started", task_id=task["id"], run_id=row["id"],
              trace_id=handle.trace_id, attempt=view["attempt"],
              launch="channel")
    return view


def _prepare_workspace(row: dict, cwd: Optional[str]) -> None:
    """D8: the provider readies the directory before the launch opens a
    session there — kimi's would otherwise stop on its Trust prompt."""
    from lib.providers.registry import build_provider  # noqa: PLC0415
    provider = build_provider((row["params"] or {}).get("provider")
                              or "claude")
    provider.prepare_workspace(Path(cwd or settings.project_root).resolve())


def _refuse_launch(row: dict, exc: Exception) -> RunRefused:
    """Settle an attempt whose launch was refused, and the refusal to raise.

    A refusal is not a crash: the attempt never started, so it is settled here
    and the task stays armed rather than stranded as `running`.
    """
    complete_run(row["id"], status=RUN_STATUS_FAILED, exit_reason=str(exc))
    return RunRefused(f"launch refused: {exc}",
                      field=getattr(exc, "field", None))


def _mark_started(task: dict, row: dict, trace_id: str) -> dict:
    view = _attach_trace(row["id"], trace_id)
    store.set_status(task["id"], TASK_STATUS_RUNNING)
    # Work beginning again over a claimed-done feature is exactly what a round
    # is. The decision reads the feature's last event alone, so a second
    # attempt inside one round appends nothing and the same run reported twice
    # costs nothing either. A spec step writes the spec, not code, so its run
    # is not a round and leaves the feature's log untouched.
    if (task.get("feature_id") is not None
            and task.get("kind") != TASK_KIND_SPEC):
        from lib.features import events as feature_events  # noqa: PLC0415
        feature_events.on_run_start(task["feature_id"], row["id"],
                                    trace_id=trace_id)
    _resolve_stuck(task["id"])
    return view


def _start_tmux(task: dict, row: dict, kwargs: dict) -> dict:
    """The tmux arm of `start_run`. No completion callback: the session is not
    this process's, so it settles on the agent's `task finish`, a Stop, or the
    next boot finding its session gone."""
    try:
        trace_id = tmux_runs.launch_attempt(
            row, _prompt(task, row["id"], row["params"]), kwargs,
            _launch_images(task))
    except tmux_runs.TmuxRunRefused as exc:
        raise _refuse_launch(row, exc) from exc
    view = _mark_started(task, row, trace_id)
    log.write("task_run_started", task_id=task["id"], run_id=row["id"],
              trace_id=trace_id, attempt=view["attempt"], launch="tmux")
    return view


def stop_tmux(view: dict) -> bool:
    """Stop a tmux attempt: kill its session, then settle it — nothing else
    will, since no callback is waiting on a session this process never owned.
    """
    killed = tmux_runs.stop(view)
    settle_gone_tmux(view, tmux_runs.STOPPED_REASON)
    return killed


def settle_gone_tmux(view: dict, reason: str) -> list[int]:
    """Fail a tmux attempt whose session is gone, and fire what that released."""
    _settled_now, fired = _settle_and_fire(
        view["task_id"], view["id"], RUN_STATUS_FAILED, reason)
    return fired


def reap_tmux_runs_at_boot() -> dict:
    """The boot half of a tmux run's lifecycle: a session tmux says is gone
    settles failed; a live or unanswerable one is left `running` (D3).

    Then every armed task a non-server process left ready is fired, which is
    how an `sdk` dependent released by a CLI-side settle gets its launch.
    """
    reaped = []
    for view in tmux_runs.open_attempts():
        try:
            if tmux_runs.liveness(view) == tmux_runs.GONE:
                settle_gone_tmux(view, tmux_runs.GONE_REASON)
                reaped.append(view["id"])
        except Exception:  # noqa: BLE001 — one row cannot sink the boot
            log.error("task_tmux_reap_failed", run_id=view["id"], exc_info=True)
    fired = fire_deferred()
    if reaped or fired:
        log.write("task_tmux_boot_reap", reaped=reaped, fired=fired)
    return {"reaped": reaped, "fired": fired}


def _mark_deferred(task_id: int) -> None:
    with SessionLocal() as session:
        row = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id,
                   AgentTaskRun.status == RUN_STATUS_ARMED)).first()
        if row is None:
            return
        params = store._decode_params(row.params_json)
        params[_DEFERRED] = True
        row.params_json = json.dumps(params)
        session.add(row)
        session.commit()


def _take_deferred() -> list[int]:
    """The tasks a non-server process released but could not launch, with the
    mark cleared — a fire that is then refused leaves an ordinary armed task,
    not one retried on every later event."""
    taken = []
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.status == RUN_STATUS_ARMED,
                   AgentTaskRun.params_json.contains(f'"{_DEFERRED}"'))).all()
        for row in rows:
            params = store._decode_params(row.params_json)
            if not params.pop(_DEFERRED, False):
                continue
            # Conditional on the text read: two server threads taking at once
            # cannot both win the same task.
            won = session.execute(
                update(AgentTaskRun)
                .where(AgentTaskRun.id == row.id,
                       AgentTaskRun.params_json == row.params_json)
                .values(params_json=json.dumps(params))).rowcount
            if won:
                taken.append(row.task_id)
        session.commit()
    return taken


def fire_deferred() -> list[int]:
    """Fire the SDK tasks a CLI-side settle left for the server (D5) — at boot
    and on every fire the server handles, so a settle that lands between a
    restart's boot pass and its first request still gets its launch."""
    fired = []
    for task_id in _take_deferred():
        try:
            if fire_if_ready(task_id):
                fired.append(task_id)
        except Exception:  # noqa: BLE001 — one task cannot sink the rest
            log.error("task_deferred_fire_failed", task_id=task_id,
                      exc_info=True)
    return fired


# ── completion ───────────────────────────────────────────────

def complete_run(run_id: int, *, status: str, exit_reason: str = "",
                 task_status: Optional[str] = None,
                 settled_by: str = SETTLED_BY_SESSION) -> bool:
    """Settle one attempt, and the task with it. Returns whether this call was
    the one that did it.

    Idempotent by `ended_at`: a second completion cannot rewrite how a run
    ended, and — because the return value gates it — cannot re-fire its
    dependents either.

    The task only follows the run while it is still `running`: a task re-armed
    by a human while the previous attempt was tearing down has moved on, and
    stamping it `done` would undo that.

    `settled_by` records which authority wrote the terminal state, so
    `reopen_for_agent_report` can later tell an inference apart from a report.
    """
    with SessionLocal() as session:
        row = session.get(AgentTaskRun, run_id)
        if row is None or row.ended_at is not None:
            return False
        task_id_of = row.task_id
        row.status = status
        row.ended_at = _now()
        row.exit_reason = (exit_reason or "")[:_REASON_MAX] or None
        row.settled_by = settled_by
        session.add(row)
        task = session.get(AgentTask, row.task_id) if task_status else None
        if task is not None and task.status == TASK_STATUS_RUNNING:
            task.status = task_status
            task.updated_at = _now()
            session.add(task)
        session.commit()
    # A run settling says *this session finished*, never *this feature
    # shipped*, so it writes no feature event at all. The two used to share a
    # column, and a settling run therefore shipped features nobody claimed.
    log.write("task_run_settled", run_id=run_id, status=status,
              task_status=task_status)
    return True


def _settled(outcome) -> tuple[str, str]:
    """`(run status, reason)` from a `RunOutcome`.

    `exited` is the one clean end: the outcome is read back from the durable
    `agent_runs` row, so anything else — `failed`, or a row still claiming to
    run — is a run that did not finish on its own terms.
    """
    status = getattr(outcome, "status", "") or ""
    detail = getattr(outcome, "detail", "") or status
    return (RUN_STATUS_DONE if status == "exited" else RUN_STATUS_FAILED,
            detail)


def _settle_and_fire(task_id: int, run_id: int, status: str, reason: str,
                     *, resync: bool = False,
                     settled_by: str = SETTLED_BY_SESSION,
                     in_server: bool = True) -> tuple[bool, list[int]]:
    """Settle, fire. The terminal write is in a `finally` because a settle
    that does not happen outlives this process, and one that happens wrong
    does not."""
    try:
        status, reason = _held_by_open_defects(task_id, run_id, status, reason)
    finally:
        ended = complete_run(run_id, status=status, exit_reason=reason,
                             task_status=_TASK_STATUS_FOR[status],
                             settled_by=settled_by)
    if not ended:
        return False, []
    if resync:
        _resync_feature(task_id)
    return True, fire_ready_dependents(task_id, in_server=in_server)


def _held_by_open_defects(task_id: int, run_id: int, status: str,
                          reason: str) -> tuple[str, str]:
    """A settle that would write `done` over an open fix defect writes
    `failed` instead.

    `finish_run` refuses the agent's own report, but a clean exit, a Stop of
    an idle run or a session ending reaches the task without one — and every
    one of them settles here, so this is the one place the check cannot be
    walked around. Regin still never closes the defect: that needs the check
    only the closer can name.
    """
    if status != RUN_STATUS_DONE:
        return status, reason
    try:
        ids = _open_fix_defects(task_id)
    except Exception:  # noqa: BLE001 — a check we cannot read is one we must not skip
        log.error("task_run_fix_defects_unreadable", run_id=run_id,
                  task_id=task_id, exc_info=True)
        return RUN_STATUS_FAILED, "ended with its fix defects unreadable"
    if not ids:
        return status, reason
    log.warn("task_run_settle_held", run_id=run_id, task_id=task_id,
             open_defects=ids)
    return RUN_STATUS_FAILED, f"ended with {_defects_named(ids)} still open"


def handle_completion(task_id: int, run_id: int, outcome) -> None:
    """The run's own completion path: settle the attempt, then fire whatever it
    unblocked.

    Arriving second is the common case, not the exception: the agent's own
    `task finish` settles the work, and the session it settled from ends here
    afterwards — `complete_run`'s `ended_at` guard makes this a no-op then.
    """
    status, reason = RUN_STATUS_FAILED, "run ended"
    try:
        status, reason = _settled(outcome)
    finally:
        _settle_and_fire(task_id, run_id, status, reason)


# ── firing ───────────────────────────────────────────────────


def _fire(task_id: int, *, in_server: bool = True) -> bool:
    """Launch one task that just became runnable. Returns whether it started.

    A dependency edge IS the grant: arming a task behind another one is the
    operator saying "run this when that lands", so the release needs no second
    opt-in. A refusal is reported and leaves the task armed — the operator can
    run it once the reason is gone.
    """
    task = store.get_task(task_id)
    if task is None or task["status"] != TASK_STATUS_ARMED:
        return False
    if deps.blocking(task_id):
        return False
    if not in_server and not _launches_detached(task_id):
        # D5: an SDK child of a short-lived process dies with it. Left armed,
        # marked, and with its edges intact, for the server's next fire.
        _mark_deferred(task_id)
        log.write("task_fire_deferred", task_id=task_id)
        return False
    try:
        start_run(task_id)
    except (RunRefused, worktree.WorktreeRefused) as exc:
        _announce_stuck(task, str(exc))
        return False
    return True


def fire_if_ready(task_id: int, *, in_server: bool = True) -> bool:
    """Resolve one task against a write that could have made it runnable.

    Completion is not the only such write: arming a task whose dependencies
    already finished, or dropping the edge that was blocking it, changes the
    same answer and would otherwise leave the task armed with nothing left to
    fire it.

    A task with no dependencies is not resolved here: nothing could ever come
    due for it, and arming one is a request to write parameters, not to run.

    The retirement runs AFTER the fire, never before: an `after` edge written
    behind a task that already settled is born satisfied, and sweeping it
    first would leave this task with no dependencies and send the guard above
    home without launching the very thing the edge was drawn to order.
    """
    if not deps.depends_on(task_id):
        return False
    try:
        return _fire(task_id, in_server=in_server)
    finally:
        deps.retire_satisfied_after(task_id)


def _launches_detached(task_id: int) -> bool:
    pending = armed_attempt(task_id)
    return pending is not None and tmux_runs.is_tmux(pending["params"])


def fire_ready_dependents(task_id: int, *, in_server: bool = True) -> list[int]:
    """Launch every dependent this completion just unblocked.

    There is no scheduler and no interval behind this: the completion that
    could change the answer is the thing that recomputes it, so a task whose
    last dependency just landed is resolved exactly once, here.

    The same completion retires the `after` edges it just honoured. Last,
    because the dependent list is read from those edges and the notice below
    reads it again — a sweep in the middle would shorten the walk it is part
    of.
    """
    dependents = deps.dependents(task_id)
    fired, deferred = _fire_each(dependents, in_server)
    if fired:
        log.write("task_dependents_fired", task_id=task_id, fired=fired)
    try:
        _announce_broken_chains(task_id, fired)
    except Exception:  # noqa: BLE001 — a notice cannot sink a completion
        log.error("task_chain_notice_failed", task_id=task_id, exc_info=True)
    # A deferred dependent keeps its edges until the server fires it.
    for dependent in dependents:
        if dependent not in deferred:
            _retire_after(dependent)
    if in_server:
        fire_deferred()
    return fired


def _fire_each(dependents: list[int], in_server: bool
               ) -> tuple[list[int], list[int]]:
    """`(fired, deferred)` — deferred being the SDK dependents a non-server
    process released but may not launch."""
    fired, deferred = [], []
    for dependent in dependents:
        try:
            if _fire(dependent, in_server=in_server):
                fired.append(dependent)
            elif not in_server and not _launches_detached(dependent):
                deferred.append(dependent)
        except Exception:  # noqa: BLE001 — one dependent cannot sink the rest
            log.error("task_fire_failed", task_id=dependent, exc_info=True)
    return fired, deferred


def _retire_after(dependent: int) -> None:
    try:
        deps.retire_satisfied_after(dependent)
    except Exception:  # noqa: BLE001 — a tidy-up cannot sink a completion
        log.error("task_after_retire_failed", task_id=dependent, exc_info=True)


def _refuse_with_open_defects(task_id: int, run_id: int) -> None:
    """A fix task reported done over a defect still open is refused.

    Otherwise the board says fixed and the ledger says open, and nothing
    reconciles them: regin never writes one side from the other. Refused
    before the report is posted, so a retry after closing is the only report.
    """
    ids = _open_fix_defects(task_id)
    if not ids:
        return
    log.warn("task_run_finish_refused", run_id=run_id, task_id=task_id,
             open_defects=ids)
    cmds = "\n".join(f"  {_close_defect_command(i)}" for i in ids)
    raise RunRefused(
        f"task #{task_id} is the fix for {_defects_named(ids)}, still open. "
        f"Close each once the fix is verified, then finish again:\n{cmds}\n"
        f"If it is not fixed, finish with --status failed instead.",
        blocking=ids)


def finish_run(run_id: int, *, status: str = RUN_STATUS_DONE,
               reason: str = "", in_server: bool = True,
               task_id: Optional[int] = None) -> dict:
    """The agent's own report, from inside its run, that the work is done.

    This is the completion a dependency chain actually waits on. A task run is
    launched steerable (`one_shot` off), so its session does not end when the
    agent stops working — it parks on an empty prompt queue until someone stops
    it, and `handle_completion` with it. Nothing downstream could ever fire.
    An armed `one_shot` is honoured here rather than at launch: the report is
    what ends the session, after the turn that made it (`run_ending`).

    So the run reports for itself, and the session it reports from stays open:
    settling the attempt is a statement about the work, not about the
    conversation. Stop still reaches the run afterwards — `_deliver` addresses
    the latest attempt, not a live one.

    Idempotent, and safe against the callback that may still arrive: both paths
    settle through `complete_run`, whose `ended_at` guard picks one winner and
    whose return value is what gates the firing.

    `task_id`, when given, is the task the caller believes it is finishing; a
    run belonging to another task is refused rather than settled.
    """
    if status not in _TASK_STATUS_FOR:
        raise ValueError(f"unknown finish status {status!r}")
    claimed = task_id
    with SessionLocal() as session:
        row = session.get(AgentTaskRun, run_id)
        task_id = row.task_id if row is not None else None
    if claimed is not None and task_id is not None and claimed != task_id:
        raise RunRefused(f"run #{run_id} belongs to task #{task_id}, "
                         f"not task #{claimed}")
    if task_id is None:
        return {"run_id": run_id, "task_id": None, "settled": False,
                "fired": []}
    if status == RUN_STATUS_DONE:
        _refuse_with_open_defects(task_id, run_id)
    # Before the default is substituted: only what the AGENT actually wrote is
    # a report. "agent signalled finish" is regin talking to itself.
    if reason.strip():
        _post_agent_report(task_id, run_id, status, reason.strip())
    reason = reason or "agent signalled finish"
    reopen_for_agent_report(run_id)
    # After the settle: an idle SDK session ends the moment it is closed, and
    # its completion callback must find the report already written.
    result = _settle_report(task_id, run_id, status, reason, in_server)
    run_ending.request_end(run_id)
    return result


def _settle_report(task_id: int, run_id: int, status: str, reason: str,
                   in_server: bool) -> dict:
    # Only this path refreshes the remote: it is the one that runs on a request
    # thread. `handle_completion` runs on the SDK loop's thread and cannot
    # spend a round trip against a tracker.
    settled, fired = _settle_and_fire(task_id, run_id, status, reason,
                                      resync=True,
                                      settled_by=SETTLED_BY_AGENT,
                                      in_server=in_server)
    log.write("task_run_finish_signalled", run_id=run_id, task_id=task_id,
              status=status, settled=settled, fired=fired)
    return {"run_id": run_id, "task_id": task_id, "settled": settled,
            "fired": fired}


def _resync_feature(task_id: int) -> None:
    """Re-read the remote issue a finished run was told to move. Never raises.

    The event is an agent reporting its own work finished — a linked run is
    launched told to move the issue's state to match what it did, so that
    report is the moment regin's copy is known stale.

    A failure is swallowed rather than propagated: the report settles the
    attempt and releases whatever waited on it, and a tracker being unreachable
    cannot be allowed to walk that back. The reason lands on the feature
    (`sync_error`), which is what its page states.
    """
    from lib.features import store as feature_store  # noqa: PLC0415
    from lib.features import sync  # noqa: PLC0415

    feature = None
    try:
        feature = feature_store.feature_of_task(task_id)
        if feature is None or not sync.is_remote(feature):
            return
        sync.refresh(feature.id)
    except Exception:  # noqa: BLE001 — a remote cannot sink the finish
        log.error("feature_resync_failed", task_id=task_id,
                  feature_id=getattr(feature, "id", None), exc_info=True)
