"""The task DAG drawn as feature lanes — the Tasks tab's Graph view.

Every task sits in the lane of its feature (or the one lane of loose tasks),
and every number the view prints is counted here over the set it draws, never
by the browser over whatever it happened to receive:

| Field          | Counted over                                                |
|----------------|-------------------------------------------------------------|
| lane done/total| the lane's drawn tasks                                      |
| chains         | weakly connected components among the drawn tasks          |
| lanes_hidden   | feature lanes in scope with no open task (open mode only)   |

The list's filters (priority, tag, source, feature, assignee, feature steps,
project) narrow the drawn set with the same WHERE clauses the feed uses
(`lib.tasks.query.apply_filters`), before "Open chains only" runs over what is
left. Whether an edge blocks is still decided against every task's status: a
filtered-out dependency that has not finished still holds its task back.

"Open chains only" keeps each connected component holding at least one open
task, then drops any lane left without an open task of its own. Archived
tasks are never drawn: archived is reached by asking for it by name, and the
graph has no such ask.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models.features import Feature
from lib.orm.models.tasks import (
    AgentTask, AgentTaskDep, TASK_STATUS_ARMED,
    TASK_STATUS_DONE,
    TASK_STATUS_IDEA, TASK_STATUS_QUEUED, TASK_STATUS_RUNNING,
)
from lib.tasks import deps, query, rows

OPEN_STATUSES = (TASK_STATUS_IDEA, TASK_STATUS_ARMED, TASK_STATUS_QUEUED,
                 TASK_STATUS_RUNNING)


@dataclass(frozen=True)
class LaneQuery:
    """What the Graph view asks for: open chains or everything, the feed's
    filters (`query.parse_filters`), and the size past which it will not draw."""

    open_only: bool = True
    filters: dict = field(default_factory=lambda: query.parse_filters({}))
    cap: Optional[int] = None


def _drawable(filters: dict) -> dict:
    return {**filters, "status": "", "include_archived": False}


def _components(ids: set[int], edges) -> list[set[int]]:
    adjacency: dict[int, set[int]] = {i: set() for i in ids}
    for task_id, dep, _kind in edges:
        if task_id in ids and dep in ids:
            adjacency[task_id].add(dep)
            adjacency[dep].add(task_id)
    seen: set[int] = set()
    out = []
    for start in sorted(ids):
        if start in seen:
            continue
        component, frontier = {start}, [start]
        while frontier:
            for nxt in adjacency[frontier.pop()] - component:
                component.add(nxt)
                frontier.append(nxt)
        seen |= component
        out.append(component)
    return out


def _open_lanes(tasks: list[AgentTask]) -> set[Optional[int]]:
    return {t.feature_id for t in tasks if t.status in OPEN_STATUSES}


def _open_chains(scoped: dict[int, AgentTask], edges) -> set[int]:
    keep: set[int] = set()
    for component in _components(set(scoped), edges):
        if any(scoped[i].status in OPEN_STATUSES for i in component):
            keep |= component
    open_lanes = _open_lanes([scoped[i] for i in keep])
    return {i for i in keep if scoped[i].feature_id in open_lanes}


def _feature_titles(session, feature_ids: set[int]) -> dict[int, str]:
    if not feature_ids:
        return {}
    return dict(session.exec(select(Feature.id, Feature.title)
                             .where(Feature.id.in_(feature_ids))).all())


def _lanes(kept: list[AgentTask], titles: dict[int, str]) -> list[dict]:
    by_lane: dict[Optional[int], list[AgentTask]] = {}
    for task in kept:
        by_lane.setdefault(task.feature_id, []).append(task)
    features = sorted(k for k in by_lane if k is not None)
    order = [*features, None] if None in by_lane else features
    return [{
        "feature_id": fid,
        "title": titles.get(fid, f"Feature {fid}") if fid else "Loose tasks",
        "done": sum(1 for t in by_lane[fid]
                    if t.status == TASK_STATUS_DONE),
        "total": len(by_lane[fid]),
    } for fid in order]


def _hidden_lanes(scoped: dict[int, AgentTask], ask: LaneQuery) -> int:
    if not ask.open_only:
        return 0
    everything = list(scoped.values())
    return len({t.feature_id for t in everything if t.feature_id}
               - _open_lanes(everything))


def _drawing(kept: list[AgentTask], keep: set[int], edges, unmet) -> dict:
    return {
        "nodes": rows.fix_defects(rows.schedules(rows.live_runs(
            [deps.node(t, sorted(unmet.get(t.id, []))) for t in kept]))),
        "edges": [{"task_id": a, "depends_on_id": b, "kind": kind,
                   "blocking": b in unmet.get(a, [])}
                  for a, b, kind in edges if a in keep and b in keep],
    }


@dataclass(frozen=True)
class _Loaded:
    statuses: dict[int, str]
    edges: list
    scoped: dict[int, AgentTask]
    keep: set[int]
    kept: list[AgentTask]
    titles: dict[int, str]


def _load(ask: LaneQuery) -> _Loaded:
    with SessionLocal() as session:
        statuses = dict(session.exec(
            select(AgentTask.id, AgentTask.status)).all())
        edges = [(row.task_id, row.depends_on_id, row.kind)
                 for row in session.exec(select(AgentTaskDep)).all()]
        scoped = {t.id: t for t in session.exec(query.apply_filters(
            select(AgentTask), _drawable(ask.filters))).all()}
        keep = _open_chains(scoped, edges) if ask.open_only else set(scoped)
        kept = [scoped[i] for i in sorted(keep)]
        titles = _feature_titles(
            session, {t.feature_id for t in kept if t.feature_id})
    return _Loaded(statuses, edges, scoped, keep, kept, titles)


def read(ask: LaneQuery) -> dict:
    """Nodes, edges, lanes and the footer's counts for one Graph view ask."""
    got = _load(ask)
    truncated = ask.cap is not None and len(got.kept) > ask.cap
    unmet = deps.unmet(got.edges, got.statuses)
    drawing = ({"nodes": [], "edges": [], "lanes": []} if truncated else {
        **_drawing(got.kept, got.keep, got.edges, unmet),
        "lanes": _lanes(got.kept, got.titles)})
    return {
        **drawing,
        "chains": len(_components(got.keep, got.edges)),
        "lanes_hidden": _hidden_lanes(got.scoped, ask),
        "open_only": ask.open_only,
        "truncated": truncated,
        "total_nodes": len(got.kept),
        "node_cap": ask.cap,
    }
