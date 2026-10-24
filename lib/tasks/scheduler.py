"""The time side of task launching: one server thread, and the gated launch.

The thread sleeps on `schedules.WAKE` until the earliest enabled
`next_fire_at`; any schedule write in this process sets the event, so there is
no polling interval. Firing on a clock is the feature here, not a fallback.

A gate (`at`/`window`) is ANDed with the dependency check. Whichever condition
is satisfied last launches: a dependency landing inside an open window goes
through `runs._fire` → `launch_gated`, and a window opening over a ready task
goes through `process` below. `UNIQUE(schedule_id, slot_at)` on the fire
ledger decides which of the two wins one window instance.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional

from lib.activity_log import get_activity_logger
from lib.orm.models.tasks import (
    FIRE_REFUSED, FIRE_SKIPPED_BLOCKED, FIRE_SKIPPED_LIVE, FIRE_STARTED,
    FIRE_VIA_SCHEDULE, SCHEDULE_KIND_AT, SCHEDULE_KIND_CRON,
    TASK_STATUS_ARCHIVED, TASK_STATUS_ARMED,
)
from lib.tasks import deps, schedules, store

log = get_activity_logger("tasks")

REFUSED_KEY = "schedule-refused:{task_id}"


def _announce_refused(task: dict, why: str) -> None:
    from lib.agent_messages import events  # noqa: PLC0415
    from lib.tasks.run_announce import TASK_READY_TRACE  # noqa: PLC0415

    events.emit(
        "task.schedule_refused", trace_id=TASK_READY_TRACE,
        title=f"Scheduled start refused: {task['title'][:80]}",
        body=(f"task {task['id']} — **{task['title']}** — reached its scheduled start, "
              f"but the launch was refused: {why}. It is still armed."),
        key=REFUSED_KEY.format(task_id=task["id"]),
        links=[{"label": "Open task", "href": f"/tasks/{task['id']}"}])


def _start(task: dict, schedule: dict, fire_id: int) -> bool:
    """Launch under a fire row already claimed. Returns whether it started."""
    from lib.agent_sdk import worktree  # noqa: PLC0415
    from lib.tasks import runs  # noqa: PLC0415

    try:
        view = runs.start_run(task["id"])
    except (runs.RunRefused, worktree.WorktreeRefused) as exc:
        schedules.settle_fire(fire_id, outcome=FIRE_REFUSED, reason=str(exc))
        _announce_refused(task, str(exc))
        log.warn("task_schedule_refused", task_id=task["id"],
                 schedule_id=schedule["id"], detail=str(exc))
        return False
    schedules.settle_fire(fire_id, run_id=(view or {}).get("id"))
    log.write("task_schedule_started", task_id=task["id"],
              schedule_id=schedule["id"], run_id=(view or {}).get("id"))
    return True


def _after_gate_start(gate: dict, now: datetime) -> None:
    if gate["kind"] == SCHEDULE_KIND_AT:
        schedules.set_next(gate["id"], None, disable=True)
    else:
        schedules.set_next(gate["id"], schedules.next_after(gate, now))


def launch_gated(task: dict, gate: dict, via: str,
                 now: Optional[datetime] = None) -> bool:
    """Launch an armed, unblocked task whose gate is open, claiming the slot.

    The caller has already checked armed + unblocked + gate open; the claim is
    what makes two paths deciding in the same window instance launch once.
    """
    from lib.tasks import runs  # noqa: PLC0415

    now = now or schedules.now_utc()
    slot = schedules.gate_slot(gate, now)
    if slot is None or runs.live_run(task["id"]) is not None:
        return False
    fire_id = schedules.claim(gate["id"], slot, via, FIRE_STARTED)
    if fire_id is None:
        return False
    started = _start(task, gate, fire_id)
    if started:
        _after_gate_start(gate, now)
    return started


def gate_waiting(task_id: int, now: Optional[datetime] = None
                 ) -> Optional[dict]:
    """The enabled gate holding this task shut right now, or None."""
    gate = schedules.active_gate(task_id)
    if gate is None or schedules.gate_open(gate, now or schedules.now_utc()):
        return None
    return gate


def _ready(task: Optional[dict]) -> bool:
    return (task is not None and task["status"] == TASK_STATUS_ARMED
            and not deps.blocking(task["id"]))


def _process_gate(spec: dict, now: datetime) -> None:
    task = store.get_task(spec["task_id"])
    started = False
    if schedules.gate_open(spec, now) and _ready(task):
        started = launch_gated(task, spec, FIRE_VIA_SCHEDULE, now)
    if not started:
        schedules.set_next(spec["id"], schedules.next_after(spec, now))


def _cron_outcome(task: dict) -> tuple[str, Optional[str]]:
    from lib.tasks import runs  # noqa: PLC0415

    if runs.live_run(task["id"]) is not None:
        return FIRE_SKIPPED_LIVE, "the previous attempt is still running"
    blocked = deps.blocking(task["id"])
    if blocked:
        return FIRE_SKIPPED_BLOCKED, "blocked by " + ", ".join(
            f"#{dep}" for dep in blocked)
    return FIRE_STARTED, None


def _process_cron(spec: dict, now: datetime) -> None:
    """Handle the newest slot at or before `now` — earlier missed ones are
    neither made up nor recorded — then advance past `now`."""
    task = store.get_task(spec["task_id"])
    if task is not None and task["status"] != TASK_STATUS_ARCHIVED:
        slot = schedules.iso(schedules.latest_cron_slot(spec, now))
        outcome, reason = _cron_outcome(task)
        fire_id = schedules.claim(spec["id"], slot, FIRE_VIA_SCHEDULE,
                                  outcome, reason)
        if fire_id is not None and outcome == FIRE_STARTED:
            _start(task, spec, fire_id)
    schedules.set_next(spec["id"], schedules.next_after(spec, now))


def process(spec: dict, now: datetime) -> None:
    if spec["kind"] == SCHEDULE_KIND_CRON:
        _process_cron(spec, now)
    else:
        _process_gate(spec, now)


def process_due(now: Optional[datetime] = None) -> int:
    """Handle every enabled schedule whose time has come. Returns how many."""
    now = now or schedules.now_utc()
    handled = 0
    for spec in schedules.due(now):
        try:
            process(spec, now)
            handled += 1
        except Exception:  # noqa: BLE001 — one schedule cannot sink the rest
            log.error("task_schedule_process_failed", schedule_id=spec["id"],
                      exc_info=True)
            schedules.set_next(spec["id"], schedules.next_after(spec, now))
    return handled


def _timeout() -> Optional[float]:
    earliest = schedules.earliest()
    if earliest is None:
        return None
    return max(0.0, (earliest - schedules.now_utc()).total_seconds())


def run_forever(stop: Optional[threading.Event] = None) -> None:
    """The scheduler thread's body. The first pass is the boot catch-up."""
    while stop is None or not stop.is_set():
        # Cleared before the pass, so a write landing mid-pass still wakes us.
        schedules.WAKE.clear()
        try:
            process_due()
        except Exception:  # noqa: BLE001 — the thread must outlive a bad pass
            log.error("task_scheduler_pass_failed", exc_info=True)
        schedules.WAKE.wait(_timeout())


def start_thread() -> threading.Thread:
    thread = threading.Thread(target=run_forever, name="task-scheduler",
                              daemon=True)
    thread.start()
    return thread
