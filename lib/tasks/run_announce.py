"""Operator-facing announcements for tasks that came due and could not start,
and for dependency chains a failed task stopped.
"""

from __future__ import annotations

from lib.activity_log import get_activity_logger
from lib.orm.models.tasks import TASK_STATUS_ARMED, TASK_STATUS_QUEUED
from lib.tasks import deps, store

log = get_activity_logger("tasks")


# A card grouping key, not a navigable session: a `task.ready` event belongs to
# no trace, so it is filed under a sentinel registered in
# `events.NON_SESSION_TRACE_IDS` and its card renders no session link.
TASK_READY_TRACE = "agent-tasks"


def _announce_stuck(task: dict, why: str) -> None:
    """Tell the operator about a task that came due and could not start.

    The only outcome of a release that a human has to act on: the task is
    runnable, the launch was refused, and it stays `armed` — so the card
    carries the refusal rather than leaving the task silently parked.
    """
    from lib.agent_messages import events

    events.emit(
        "task.ready", trace_id=TASK_READY_TRACE,
        title=f"Task ready: {task['title'][:80]}",
        body=(f"Every dependency of task {task['id']} — **{task['title']}** — "
              f"is done, but it could not start: {why}. It is still armed."),
        key=f"task-ready:{task['id']}",
        links=[{"label": "Open Tasks", "href": "/tasks"}], once=True)


#: The statuses a dependent can hold while a failed slice is what stands
#: between it and running. Invariant 6 is about the cascade, not about `armed`.
_PARKED = (TASK_STATUS_ARMED, TASK_STATUS_QUEUED)


def _announce_chain_stopped(blocker: dict, parked: list[int]) -> None:
    """Tell the operator which slice stopped a cascade.

    `blocking` treats "not done" as one answer, so a failed dependency parks
    its dependents in `armed` looking exactly like a normal wait. It is not
    one: nothing is coming, and only a human re-arming that slice will start
    the chain again. The card names the slice rather than the dependents
    because that is the row to act on.
    """
    from lib.agent_messages import events

    waiting = ", ".join(str(task_id) for task_id in parked)
    events.emit(
        "task.chain_stopped", trace_id=TASK_READY_TRACE,
        title=f"Chain stopped at: {blocker['title'][:80]}",
        body=(f"Task {blocker['id']} — **{blocker['title']}** — failed, and "
              f"{len(parked)} task(s) still wait on it: {waiting}. They stay "
              f"armed, but nothing will release them until "
              f"{blocker['id']} runs again."),
        key=f"chain-stopped:{blocker['id']}",
        links=[{"label": "Open Tasks", "href": "/tasks"}], once=True)


def _announce_broken_chains(task_id: int, fired: list[int]) -> None:
    """Report every failed slice that is holding a dependent of `task_id`.

    Read after the firing, not instead of it: a dependent that launched is not
    blocked by anything, and a dependent still waiting on an *unfinished*
    slice is waiting normally. Only a `failed` blocker is the broken chain.

    The failed slice is not necessarily the one that just completed — a
    dependent can wait on two slices, one landing now and one that failed
    earlier — so the blocker is looked up per dependent.

    `failed_blockers` reads only `requires` edges, so a dependent queued behind
    unrelated work by an `after` edge raises no card: it was already released
    by the failure.
    """
    by_blocker: dict[int, tuple[dict, list[int]]] = {}
    for dependent in deps.dependents(task_id):
        if dependent in fired:
            continue
        task = store.get_task(dependent)
        # Parked, not merely unstarted: `idea` is a backlog row nobody asked
        # to run, and anything settled or live is not waiting on this edge.
        if task is None or task["status"] not in _PARKED:
            continue
        for blocker in deps.failed_blockers(dependent):
            by_blocker.setdefault(blocker["id"], (blocker, []))[1].append(
                dependent)
    for blocker, parked in by_blocker.values():
        _announce_chain_stopped(blocker, parked)
        log.write("task_chain_stopped", task_id=blocker["id"], parked=parked)
