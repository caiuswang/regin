"""The task DAG — `agent_task_deps` and the questions it answers.

Two of them, in both directions: what still blocks a task, and what a finished
task just unblocked. The second is why firing needs no scheduler — the run
completion that could change the answer is the thing that recomputes it.

Acyclicity is enforced here, at write time, by walking the graph the request
*would* produce before inserting it. A cycle rejected on insert is a cycle no
reader has to defend against, and no traversal below carries a visited-guard
for one.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import delete, select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, AgentTaskDep, TASK_STATUS_ARMED, TASK_STATUS_DONE,
)
from lib.tasks.capture import CaptureRejected

log = get_activity_logger("tasks")

MAX_DEPS = 50


class DepRejected(ValueError):
    """An edge the DAG cannot take — a 400 naming the offending pair."""

    def __init__(self, message: str, pair: tuple[int, int]) -> None:
        super().__init__(message)
        self.pair = pair


def parse_dep_ids(payload) -> list[int]:
    """The `depends_on` list of a `PUT /deps` body, deduplicated in order."""
    if not isinstance(payload, dict):
        raise CaptureRejected("body must be a JSON object")
    unknown = sorted(set(payload) - {"depends_on"})
    if unknown:
        raise CaptureRejected(f"unknown field: {unknown[0]}", unknown[0])
    raw = payload.get("depends_on")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CaptureRejected("depends_on must be a list of task ids",
                              "depends_on")
    if len(raw) > MAX_DEPS:
        raise CaptureRejected(f"depends_on exceeds {MAX_DEPS} ids",
                              "depends_on")
    out: list[int] = []
    for item in raw:
        if not isinstance(item, int) or isinstance(item, bool):
            raise CaptureRejected("depends_on must be a list of task ids",
                                  "depends_on")
        if item not in out:
            out.append(item)
    return out


def _edges(session) -> dict[int, set[int]]:
    """`{task: {tasks it depends on}}` for the whole graph."""
    out: dict[int, set[int]] = {}
    for row in session.exec(select(AgentTaskDep)).all():
        out.setdefault(row.task_id, set()).add(row.depends_on_id)
    return out


def _reaches(edges: dict[int, set[int]], start: int, target: int) -> bool:
    """Whether `target` is reachable from `start` by following dependencies."""
    seen: set[int] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))
    return False


def _refuse_bad_edges(session, task_id: int, wanted: list[int]) -> None:
    """Reject a self-edge, an unknown endpoint, or a cycle — by pair.

    The cycle test runs against the graph the request *would* produce, not the
    stored one: replacing a task's whole dependency set can both remove the
    edge that made a cycle and add the one that makes another.

    The run-of refusal that used to sit here went with the two-level grain: a
    feature is not a task any more, so there is no pair that could carry both
    "waits for" and "is a run of".
    """
    for dep in wanted:
        if dep == task_id:
            raise DepRejected(
                f"a task cannot depend on itself ({task_id} → {dep})",
                (task_id, dep))
        other = session.get(AgentTask, dep)
        if other is None:
            raise DepRejected(f"unknown task {dep} ({task_id} → {dep})",
                              (task_id, dep))
    edges = _edges(session)
    edges[task_id] = set(wanted)
    for dep in wanted:
        if _reaches(edges, dep, task_id):
            raise DepRejected(f"{task_id} → {dep} closes a cycle",
                              (task_id, dep))


def set_deps(task_id: int, wanted: list[int]) -> Optional[list[int]]:
    """Replace a task's dependency set. None when the task is unknown.

    Replacement rather than append: the set is what the caller sent, so an
    edge dropped from the list is dropped from the graph.
    """
    with SessionLocal() as session:
        if session.get(AgentTask, task_id) is None:
            return None
        _refuse_bad_edges(session, task_id, wanted)
        session.exec(delete(AgentTaskDep)
                     .where(AgentTaskDep.task_id == task_id))
        for dep in wanted:
            session.add(AgentTaskDep(task_id=task_id, depends_on_id=dep))
        session.commit()
    log.write("task_deps_set", task_id=task_id, count=len(wanted))
    return wanted


def depends_on(task_id: int) -> list[int]:
    """The ids `task_id` waits for."""
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskDep.depends_on_id)
            .where(AgentTaskDep.task_id == task_id)).all()
    return sorted(rows)


def dependents(task_id: int) -> list[int]:
    """The ids waiting for `task_id` — the other direction."""
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskDep.task_id)
            .where(AgentTaskDep.depends_on_id == task_id)).all()
    return sorted(rows)


def blocking(task_id: int) -> list[int]:
    """The dependencies of `task_id` that are not `done` yet.

    Empty means the task is unblocked — which is not the same as runnable; a
    task nobody armed has nothing to run.
    """
    with SessionLocal() as session:
        deps = session.exec(
            select(AgentTaskDep.depends_on_id)
            .where(AgentTaskDep.task_id == task_id)).all()
        if not deps:
            return []
        done = set(session.exec(
            select(AgentTask.id)
            .where(AgentTask.id.in_(deps))
            .where(AgentTask.status == TASK_STATUS_DONE)).all())
    return sorted(set(deps) - done)


def _node(task: AgentTask, blocked_by: list[int]) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "blocked": bool(blocked_by),
        "blocked_by": blocked_by,
        "runnable": task.status == TASK_STATUS_ARMED and not blocked_by,
    }


def _unmet(edges: list[tuple[int, int]], done: set[int]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for task_id, dep in edges:
        if dep not in done:
            out.setdefault(task_id, []).append(dep)
    return out


# Past this many nodes a layered drawing is unreadable at any zoom, and the SVG
# is tens of thousands of pixels wide. The response says so and asks the reader
# to focus on a root instead of shipping a picture nobody can use.
#
# Opt-in, not default: the dependency EDITOR reads the same endpoint for its
# candidate list, and a cap applied there would silently offer no dependencies
# at all on exactly the backlog where they matter most. Only a caller that is
# drawing passes `cap`.
GRAPH_NODE_CAP = 150
DEFAULT_DEPTH = 2
MAX_DEPTH = 6


def _neighbourhood(root: int, edges: list[tuple[int, int]],
                   depth: int) -> set[int]:
    """Every task within `depth` hops of `root`, in BOTH directions.

    Both, because the two questions a reader has about a focused task are what
    it waits for and what waits on it — an ancestors-only walk answers half of
    one of them.
    """
    adjacency: dict[int, set[int]] = {}
    for task_id, dep in edges:
        adjacency.setdefault(task_id, set()).add(dep)
        adjacency.setdefault(dep, set()).add(task_id)
    seen = {root}
    frontier = {root}
    for _ in range(depth):
        nxt = {n for node in frontier for n in adjacency.get(node, ())} - seen
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
    return seen


def _scoped(tasks, edges: list[tuple[int, int]], root: Optional[int],
            depth: Optional[int], status: Optional[str]) -> set[int]:
    """The task ids a focus and a status filter leave standing."""
    keep = {t.id for t in tasks}
    if root is not None:
        keep &= _neighbourhood(
            root, edges, min(max(1, depth or DEFAULT_DEPTH), MAX_DEPTH))
    if status:
        # The root survives its own filter: a focus view that can hide the task
        # it is focused on is answering a different question than it was asked.
        keep &= {t.id for t in tasks if t.status == status or t.id == root}
    return keep


def graph(*, root: Optional[int] = None, depth: Optional[int] = None,
          status: Optional[str] = None, cap: Optional[int] = None) -> dict:
    """The DAG, or the neighbourhood of one task in it.

    Computed in one pass over both tables rather than per node: a per-node
    query would be a fan-out over a graph that is cheap to hold whole, and the
    scoping below is applied to the result rather than to the query for the
    same reason — the walk needs edges the filter would have hidden.

    `truncated` is a real answer, not an error: an unfocused graph past `cap`
    returns its size and no nodes, so the view can ask for a root instead of
    drawing something illegible. A caller that passes no `cap` is not drawing
    and is never truncated.
    """
    with SessionLocal() as session:
        tasks = session.exec(select(AgentTask)).all()
        edges = [(row.task_id, row.depends_on_id)
                 for row in session.exec(select(AgentTaskDep)).all()]
    unmet = _unmet(edges, {t.id for t in tasks
                           if t.status == TASK_STATUS_DONE})

    keep = _scoped(tasks, edges, root, depth, status)
    total = len(keep)
    truncated = cap is not None and root is None and total > cap
    if truncated:
        keep = set()

    nodes = [_node(task, sorted(unmet.get(task.id, [])))
             for task in tasks if task.id in keep]
    kept_edges = [{"task_id": a, "depends_on_id": b} for a, b in edges
                  if a in keep and b in keep]
    log.read("task_graph_read", nodes=len(nodes), edges=len(kept_edges),
             root=root, truncated=truncated)
    return {
        "nodes": nodes,
        "edges": kept_edges,
        "root": root,
        "truncated": truncated,
        "total_nodes": total,
        "node_cap": cap,
    }
