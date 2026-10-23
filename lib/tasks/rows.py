"""What a task row carries beyond its own columns, one query per fact per page.

Every helper here takes a page of serialized tasks and hangs one fact off each
row from a single grouped read over the page's ids. A read per row is the N+1
that makes a list slow exactly when the backlog gets interesting.
"""

from __future__ import annotations

from sqlmodel import func, select

from lib.orm import SessionLocal
from lib.orm.models.defects import STATUS_OPEN, Defect
from lib.orm.models.features import Feature
from lib.orm.models.tasks import (
    AgentTask, AgentTaskDep, AgentTaskRun, RUN_OPEN_STATUSES, RUN_STATUS_ARMED,
    TASK_STATUS_ARCHIVED, TASK_STATUS_RUNNING,
)
from lib.tasks import deps as deps_module
from lib.tasks import verbs

ACTION_RUN = verbs.RUN


def next_action(status: str, blocked_by: list[int]):
    """The one action a row offers. `None` is a row with nothing to do: a
    settled task, or one still waiting on a dependency."""
    return verbs.primary(status, blocked_by)


def _ids(tasks: list[dict]) -> list[int]:
    return [t["id"] for t in tasks]


def feature_titles(tasks: list[dict]) -> list[dict]:
    """`feature_title` beside `feature_id`.

    Resolved, never stored on the task: `features.title` is the one home of
    that name, so a rename shows on every step at once.
    """
    ids = {t["feature_id"] for t in tasks if t.get("feature_id") is not None}
    titles = {}
    if ids:
        with SessionLocal() as session:
            titles = dict(session.exec(
                select(Feature.id, Feature.title)
                .where(Feature.id.in_(ids))).all())
    for task in tasks:
        task["feature_title"] = titles.get(task.get("feature_id"))
    return tasks


def fix_defects(tasks: list[dict]) -> list[dict]:
    """`fixes_defects`: the defects pointing at each task.

    The pointer lives on the defect (`defects.fix_task_id`), so without this
    a task created from a defect cannot name the defect it closes.

    `fixes_open_defects` is the subset still open — what Mark done names in
    its confirm, because marking the task done never closes them.
    """
    ids = _ids(tasks)
    by_task: dict[int, list[int]] = {}
    open_by_task: dict[int, list[int]] = {}
    if ids:
        with SessionLocal() as session:
            for defect_id, task_id, status in session.exec(
                    select(Defect.id, Defect.fix_task_id, Defect.status)
                    .where(Defect.fix_task_id.in_(ids))
                    .order_by(Defect.id)).all():
                by_task.setdefault(task_id, []).append(defect_id)
                if status == STATUS_OPEN:
                    open_by_task.setdefault(task_id, []).append(defect_id)
    for task in tasks:
        task["fixes_defects"] = by_task.get(task["id"], [])
        task["fixes_open_defects"] = open_by_task.get(task["id"], [])
    return tasks


def run_counts(tasks: list[dict]) -> list[dict]:
    """`run_count` and the pending attempt per row.

    `armed` rides along because the arm sheet seeds its draft from whatever the
    task was last armed with, and those parameters live on the attempt — a row
    that could not carry them would open an empty sheet over an armed task.
    """
    if not tasks:
        return tasks
    from lib.tasks import runs  # noqa: PLC0415

    ids = _ids(tasks)
    with SessionLocal() as session:
        attempts = dict(session.exec(
            select(AgentTaskRun.task_id, func.count(AgentTaskRun.id))
            .where(AgentTaskRun.task_id.in_(ids),
                   AgentTaskRun.status != RUN_STATUS_ARMED)
            .group_by(AgentTaskRun.task_id)).all())
        pending = {row.task_id: row for row in session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id.in_(ids),
                   AgentTaskRun.status == RUN_STATUS_ARMED)).all()}
    for row in tasks:
        row["run_count"] = attempts.get(row["id"], 0)
        armed = pending.get(row["id"])
        row["armed"] = runs.serialize_attempt(armed) if armed else None
    return tasks


def _unmet_by_task(ids: list[int]) -> tuple[dict[int, list[int]], dict]:
    with SessionLocal() as session:
        edges = session.exec(
            select(AgentTaskDep.task_id, AgentTaskDep.depends_on_id,
                   AgentTaskDep.kind)
            .where(AgentTaskDep.task_id.in_(ids))).all()
        wanted = {dep for _, dep, _ in edges}
        status = dict(session.exec(
            select(AgentTask.id, AgentTask.status)
            .where(AgentTask.id.in_(wanted))).all()) if wanted else {}
    unmet = deps_module.unmet(edges, status)
    return {task: sorted(found) for task, found in unmet.items()}, status


def dependencies(tasks: list[dict]) -> list[dict]:
    """`blocked_by`, `waits_on`, the row's one `action` and its `menu`.

    `waits_on` names the lowest-numbered unmet dependency and its status, so a
    row can read "waits on #408 (running)" without a second read.
    """
    ids = _ids(tasks)
    unmet, status = _unmet_by_task(ids) if ids else ({}, {})
    for task in tasks:
        blocked_by = unmet.get(task["id"], [])
        task["blocked_by"] = blocked_by
        task["waits_on"] = ({"id": blocked_by[0],
                             "status": status.get(blocked_by[0])}
                            if blocked_by else None)
        task["action"] = next_action(task["status"], blocked_by)
        task["menu"] = verbs.menu(task["status"], blocked_by)
    return tasks


def live_runs(tasks: list[dict]) -> list[dict]:
    """`live`: the open attempt of a running task — what "Watch" opens."""
    ids = [t["id"] for t in tasks if t["status"] == TASK_STATUS_RUNNING]
    open_runs: dict[int, AgentTaskRun] = {}
    if ids:
        with SessionLocal() as session:
            for run in session.exec(
                    select(AgentTaskRun)
                    .where(AgentTaskRun.task_id.in_(ids),
                           AgentTaskRun.status.in_(RUN_OPEN_STATUSES))
                    .order_by(AgentTaskRun.id)).all():
                open_runs[run.task_id] = run
    for task in tasks:
        run = open_runs.get(task["id"])
        task["live"] = (None if run is None else
                        {"run_id": run.id, "trace_id": run.trace_id,
                         "attempt": run.attempt, "started_at": run.started_at})
    return tasks


def steps(tasks: list[dict]) -> list[dict]:
    """`step`: `{n, of}` — this task's place among its feature's steps.

    Ordered by id, the order the slices were transcribed in, over the same
    non-archived population the feature's task count reads.
    """
    features = {t["feature_id"] for t in tasks
                if t.get("feature_id") is not None}
    order: dict[int, list[int]] = {}
    if features:
        with SessionLocal() as session:
            for task_id, feature_id in session.exec(
                    select(AgentTask.id, AgentTask.feature_id)
                    .where(AgentTask.feature_id.in_(features),
                           AgentTask.status != TASK_STATUS_ARCHIVED)
                    .order_by(AgentTask.id)).all():
                order.setdefault(feature_id, []).append(task_id)
    for task in tasks:
        siblings = order.get(task.get("feature_id"), [])
        task["step"] = ({"n": siblings.index(task["id"]) + 1,
                         "of": len(siblings)}
                        if task["id"] in siblings else None)
    return tasks


def schedules(tasks: list[dict]) -> list[dict]:
    """`schedule`: the row's schedule summary, or None.

    It carries `gate_open`, so a row armed and unblocked but behind a shut
    gate can say it waits on time rather than looking ready.
    """
    from lib.tasks import schedules as schedule_store  # noqa: PLC0415

    by_task = schedule_store.summaries(_ids(tasks))
    for task in tasks:
        task["schedule"] = by_task.get(task["id"])
    return tasks


def next_step_facts(tasks: list[dict]) -> list[dict]:
    """Everything a next-step row renders: its feature, its step, what it
    fixes, its attempts, what it waits on, its schedule, and the one action
    it offers."""
    for enrich in (feature_titles, fix_defects, run_counts, dependencies,
                   live_runs, steps, schedules):
        enrich(tasks)
    return tasks
