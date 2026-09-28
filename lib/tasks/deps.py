"""The task DAG — `agent_task_deps` and the questions it answers.

Two of them, in both directions: what still blocks a task, and what a finished
task just unblocked. The second is why firing needs no scheduler — the run
completion that could change the answer is the thing that recomputes it.

An edge also says which of two things it means. `requires` waits for its
dependency to succeed; `after` waits only for it to have settled, because that
edge is an ordering the operator drew over work that does not consume itself.
`_satisfied` is the one place the two differ.

Acyclicity is enforced here, at write time, by walking the graph the request
*would* produce before inserting it. A cycle rejected on insert is a cycle no
reader has to defend against, and no traversal below carries a visited-guard
for one.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple, Optional, Union

from sqlmodel import delete, select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, AgentTaskDep, DEP_KIND_AFTER, DEP_KIND_REQUIRES, DEP_KINDS,
    TASK_STATUS_ARMED, TASK_STATUS_DONE, TASK_STATUS_FAILED,
)
from lib.tasks import verbs
from lib.tasks.capture import CaptureRejected

log = get_activity_logger("tasks")

MAX_DEPS = 50

#: A dependency that settled this way satisfies an `after` edge. It is the
#: whole difference between the two kinds: `requires` waits for success,
#: `after` waits for the dependency to have stopped, whichever way.
_SETTLED_FOR_AFTER = (TASK_STATUS_DONE, TASK_STATUS_FAILED)


class DepEdge(NamedTuple):
    """One requested edge — the id waited for, and what the wait means."""

    id: int
    kind: str = DEP_KIND_REQUIRES


class DepRejected(ValueError):
    """An edge the DAG cannot take — a 400 naming the offending pair."""

    def __init__(self, message: str, pair: tuple[int, int]) -> None:
        super().__init__(message)
        self.pair = pair


def _parse_item(item) -> DepEdge:
    """One `depends_on` entry: a bare id, or `{"id", "kind"}`.

    A bare id is `requires` because that is the rule every edge was written
    under before the column existed — a caller that says nothing is asking for
    the strict wait, not opting into the loose one.
    """
    if isinstance(item, int) and not isinstance(item, bool):
        return DepEdge(item, DEP_KIND_REQUIRES)
    if isinstance(item, dict):
        unknown = sorted(set(item) - {"id", "kind"})
        if unknown:
            raise CaptureRejected(
                f"unknown dependency field: {unknown[0]}", "depends_on")
        task_id = item.get("id")
        if not isinstance(task_id, int) or isinstance(task_id, bool):
            raise CaptureRejected("a dependency needs an integer id",
                                  "depends_on")
        kind = item.get("kind", DEP_KIND_REQUIRES)
        if kind not in DEP_KINDS:
            raise CaptureRejected(
                f"dependency kind must be one of {list(DEP_KINDS)}",
                "depends_on")
        return DepEdge(task_id, kind)
    raise CaptureRejected(
        "depends_on takes task ids or {id, kind} objects", "depends_on")


def parse_deps(payload) -> list[DepEdge]:
    """The `depends_on` list of a `PUT /deps` body, as edges, in order.

    A repeated id is NOT collapsed here: two entries for one pair may disagree
    about the kind, and picking a winner silently would make the stored edge
    depend on list order. `set_deps` refuses the pair instead.
    """
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
    out: list[DepEdge] = []
    for item in raw:
        edge = _parse_item(item)
        if edge not in out:
            out.append(edge)
    return out


def as_edges(wanted: Iterable[Union[int, DepEdge, tuple]]) -> list[DepEdge]:
    """Coerce a caller's list to edges. A bare id is a `requires` edge.

    For Python callers, which the HTTP body never reaches: a bad kind here is
    a programming error, not a request to refuse, so it raises plainly rather
    than as a `DepRejected` whose `pair` would have to be invented.
    """
    out: list[DepEdge] = []
    for item in wanted:
        edge = item if isinstance(item, DepEdge) else (
            DepEdge(*item) if isinstance(item, tuple) else DepEdge(int(item)))
        if edge.kind not in DEP_KINDS:
            raise ValueError(f"unknown dependency kind {edge.kind!r}")
        out.append(edge)
    return out


def retire_satisfied_after(task_id: int) -> list[int]:
    """Drop `task_id`'s `after` edges that have nothing left to hold back.

    An `after` edge is scaffolding, not a fact. It says "do not start this
    until that one has stopped", and the moment that one stops the sentence is
    true forever — keeping the row would leave the DAG accumulating orderings
    that describe no relationship, which is the shape this kind exists to stop
    drawing. A `requires` edge is the opposite and is never retired: what a
    task was built on is provenance, and the graph is where it is recorded.

    Scoped to one task and called from the write that could have satisfied an
    edge, so there is no sweep and no interval. It is a garbage collection of
    edges `blocking()` already ignores, not a second opinion about them: if
    the two ever disagree, `blocking()` decides and nothing hangs.
    """
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskDep.depends_on_id)
            .where(AgentTaskDep.task_id == task_id)
            .where(AgentTaskDep.kind == DEP_KIND_AFTER)).all()
        if not rows:
            return []
        settled = sorted(session.exec(
            select(AgentTask.id)
            .where(AgentTask.id.in_(set(rows)))
            .where(AgentTask.status.in_(_SETTLED_FOR_AFTER))).all())
        if not settled:
            return []
        session.exec(delete(AgentTaskDep)
                     .where(AgentTaskDep.task_id == task_id)
                     .where(AgentTaskDep.kind == DEP_KIND_AFTER)
                     .where(AgentTaskDep.depends_on_id.in_(settled)))
        session.commit()
    log.write("task_after_edges_retired", task_id=task_id, retired=settled)
    return settled


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


def _refuse_bad_edges(session, task_id: int, wanted: list[DepEdge]) -> None:
    """Reject a self-edge, an unknown endpoint, a cycle, or one pair sent
    twice with two kinds — by pair.

    The cycle test runs against the graph the request *would* produce, not the
    stored one: replacing a task's whole dependency set can both remove the
    edge that made a cycle and add the one that makes another. It walks both
    kinds, because an `after` edge is still an edge and a loop through one
    would still hang a layered drawing.

    The run-of refusal that used to sit here went with the two-level grain: a
    feature is not a task any more, so there is no pair that could carry both
    "waits for" and "is a run of".
    """
    seen: dict[int, str] = {}
    for dep, kind in wanted:
        if dep == task_id:
            raise DepRejected(
                f"a task cannot depend on itself ({task_id} → {dep})",
                (task_id, dep))
        if seen.setdefault(dep, kind) != kind:
            raise DepRejected(
                f"{task_id} → {dep} was sent as both {seen[dep]!r} and "
                f"{kind!r}", (task_id, dep))
        other = session.get(AgentTask, dep)
        if other is None:
            raise DepRejected(f"unknown task {dep} ({task_id} → {dep})",
                              (task_id, dep))
    edges = _edges(session)
    edges[task_id] = {edge.id for edge in wanted}
    for dep, _kind in wanted:
        if _reaches(edges, dep, task_id):
            raise DepRejected(f"{task_id} → {dep} closes a cycle",
                              (task_id, dep))


def set_deps(task_id: int,
             wanted: Iterable[Union[int, DepEdge, tuple]]
             ) -> Optional[list[DepEdge]]:
    """Replace a task's dependency set. None when the task is unknown.

    Replacement rather than append, and the replacement covers the kind: the
    set is what the caller sent, so an edge dropped from the list is dropped
    from the graph and an edge re-sent as `after` stops being a `requires`.

    Bare ids are still accepted — they read as `requires`, which is what every
    edge meant before the kind existed.
    """
    edges = as_edges(wanted)
    with SessionLocal() as session:
        if session.get(AgentTask, task_id) is None:
            return None
        _refuse_bad_edges(session, task_id, edges)
        session.exec(delete(AgentTaskDep)
                     .where(AgentTaskDep.task_id == task_id))
        for dep, kind in edges:
            session.add(AgentTaskDep(task_id=task_id, depends_on_id=dep,
                                     kind=kind))
        session.commit()
    log.write("task_deps_set", task_id=task_id, count=len(edges),
              after=sum(1 for e in edges if e.kind == DEP_KIND_AFTER))
    return edges


def depends_on(task_id: int) -> list[int]:
    """The ids `task_id` waits for."""
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskDep.depends_on_id)
            .where(AgentTaskDep.task_id == task_id)).all()
    return sorted(rows)


def dep_edges(task_id: int) -> list[DepEdge]:
    """The edges `task_id` still waits on, id and kind, lowest id first."""
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskDep.depends_on_id, AgentTaskDep.kind)
            .where(AgentTaskDep.task_id == task_id)
            .order_by(AgentTaskDep.depends_on_id)).all()
    return [DepEdge(dep, kind) for dep, kind in rows]


def dependents(task_id: int) -> list[int]:
    """The ids waiting for `task_id` — the other direction."""
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskDep.task_id)
            .where(AgentTaskDep.depends_on_id == task_id)).all()
    return sorted(rows)


def _satisfied(kind: str, status: Optional[str]) -> bool:
    """Whether a dependency in `status` releases an edge of this `kind`."""
    if kind == DEP_KIND_AFTER:
        return status in _SETTLED_FOR_AFTER
    return status == TASK_STATUS_DONE


def blocking(task_id: int) -> list[int]:
    """The dependencies of `task_id` that have not satisfied their edge.

    Which is not the same question for both kinds: a `requires` edge waits for
    `done`, an `after` edge waits only for the dependency to have settled. A
    failed slice therefore holds back the work that consumed it and releases
    the unrelated work that was merely queued behind it.

    Empty means the task is unblocked — which is not the same as runnable; a
    task nobody armed has nothing to run.
    """
    with SessionLocal() as session:
        rows = session.exec(
            select(AgentTaskDep.depends_on_id, AgentTaskDep.kind)
            .where(AgentTaskDep.task_id == task_id)).all()
        if not rows:
            return []
        status = dict(session.exec(
            select(AgentTask.id, AgentTask.status)
            .where(AgentTask.id.in_({dep for dep, _ in rows}))).all())
    return sorted({dep for dep, kind in rows
                   if not _satisfied(kind, status.get(dep))})


def failed_blockers(task_id: int) -> list[dict]:
    """The dependencies of `task_id` that settled `failed`, id and title.

    The half of `blocking` that is not a wait. An unfinished dependency will
    fire this task when it lands; a failed one never will, so the two have to
    be told apart by anything that reports why a task is parked.

    Only `requires` edges are read. A failed `after` dependency is not holding
    anything back — the edge was an ordering over unrelated work — so calling
    it a broken chain would name a task the operator has no reason to re-arm.
    """
    with SessionLocal() as session:
        wanted = session.exec(
            select(AgentTaskDep.depends_on_id)
            .where(AgentTaskDep.task_id == task_id)
            .where(AgentTaskDep.kind == DEP_KIND_REQUIRES)).all()
        if not wanted:
            return []
        rows = session.exec(
            select(AgentTask)
            .where(AgentTask.id.in_(set(wanted)))
            .where(AgentTask.status == TASK_STATUS_FAILED)
            .order_by(AgentTask.id)).all()
    return [{"id": row.id, "title": row.title} for row in rows]


def node(task: AgentTask, blocked_by: list[int]) -> dict:
    return {
        "id": task.id,
        "feature_id": task.feature_id,
        "title": task.title,
        "status": task.status,
        "blocked": bool(blocked_by),
        "blocked_by": blocked_by,
        "runnable": task.status == TASK_STATUS_ARMED and not blocked_by,
        "action": verbs.primary(task.status, blocked_by),
        "waits_on": blocked_by[0] if blocked_by else None,
    }


def unmet(edges: list[tuple[int, int, str]],
           status: dict[int, str]) -> dict[int, list[int]]:
    """`{task: [deps still holding it back]}`, resolved per edge kind."""
    out: dict[int, list[int]] = {}
    for task_id, dep, kind in edges:
        if not _satisfied(kind, status.get(dep)):
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


def _neighbourhood(root: int, edges: list[tuple[int, int, str]],
                   depth: int) -> set[int]:
    """Every task within `depth` hops of `root`, in BOTH directions.

    Both, because the two questions a reader has about a focused task are what
    it waits for and what waits on it — an ancestors-only walk answers half of
    one of them.
    """
    adjacency: dict[int, set[int]] = {}
    for task_id, dep, _kind in edges:
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


def _scoped(tasks, edges: list[tuple[int, int, str]], root: Optional[int],
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


def _over_cap(total: int, root: Optional[int], cap: Optional[int]) -> bool:
    """Whether a drawing caller asked for more nodes than it can render.

    A focused walk is never truncated: the reader already narrowed it, and
    answering their focus with a size is answering a question they did not ask.
    """
    return cap is not None and root is None and total > cap


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
        edges = [(row.task_id, row.depends_on_id, row.kind)
                 for row in session.exec(select(AgentTaskDep)).all()]
    unmet_deps = unmet(edges, {t.id: t.status for t in tasks})

    scoped = _scoped(tasks, edges, root, depth, status)
    total = len(scoped)
    truncated = _over_cap(total, root, cap)
    keep = set() if truncated else scoped

    nodes = [node(task, sorted(unmet_deps.get(task.id, [])))
             for task in tasks if task.id in keep]
    kept_edges = [{"task_id": a, "depends_on_id": b, "kind": kind}
                  for a, b, kind in edges if a in keep and b in keep]
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
