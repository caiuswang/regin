"""The Overview tab: the whole pipeline under one scope, from one request.

| Panel           | Drawn from                                         | Order                                   | Cap |
|-----------------|----------------------------------------------------|-----------------------------------------|-----|
| loop            | the three tab populations, split into their parts  | Features, Tasks, Defects                | —   |
| features_in_flight | features at spec or building                    | last activity, desc                     | 5   |
| running         | running tasks, feature steps included              | latest attempt started, desc            | 6   |
| run_next        | armed tasks, feature steps included                | dependency-clear first, priority, id    | 6   |
| needs_decision  | the triage queue (`defects.store.in_queue`), unscoped | newest first                         | 3   |
| recently_done   | done tasks finished this week, feature steps included | newest finished first (Done group) | 5   |
| projects        | scope `all` only — `projects.panel`                | attention                               | 5   |
| project_head / project_features | scope = one project                | last activity                           | 5   |

Every loop stage sums its parts, and the Tasks and Features stages are the
tab strip's own statements (`lib.pipeline.counts`), so a loop total and the
tab count under the same scope cannot disagree. Features in flight reads the
same feature index under a two-stage filter, so its `total` is the Features
stage's Spec and Building parts summed.

Run next is not the Tasks tab's population: the tab lists loose tasks and
reaches a feature's steps from the feature, while "what can run next" is
mostly those steps. Its `total` therefore counts armed and queued tasks of
every grain, and says so by being its own number. Running reads the same
every-grain population, so its `total` is the number of agents working.
"""

from __future__ import annotations

import json

from sqlalchemy import case
from sqlmodel import func, select

from lib.activity_log import get_activity_logger
from lib.defects import store as defect_store
from lib.features import events as events_module
from lib.features import index as feature_index
from lib.orm import SessionLocal
from lib.orm.models.defects import STATUS_CLOSED, STATUS_OPEN, Defect
from lib.orm.models.features import Feature
from lib.orm.models.trace import Session as TraceSession, TurnUsage
from lib.orm.models.tasks import (
    AgentTask, TASK_KIND_FIX, TASK_STATUS_ARMED,
    TASK_STATUS_DONE, TASK_STATUS_FAILED, TASK_STATUS_IDEA,
    TASK_STATUS_QUEUED, TASK_STATUS_RUNNING,
)
from lib.pipeline import counts as counts_module
from lib.pipeline import projects as projects_module
from lib.pipeline.counts import SCOPE_ALL, SCOPE_NONE
from lib.tasks import next_step
from lib.tasks import query as task_query
from lib.tasks import rows as task_rows
from lib.tasks import store as task_store
from lib.workspaces import scope as ws_scope

log = get_activity_logger("features")

RUN_NEXT_CAP = 6
RUNNING_CAP = 6
NEEDS_DECISION_CAP = 3
RECENTLY_DONE_CAP = 5
PROJECT_FEATURES_CAP = 5
FEATURES_IN_FLIGHT_CAP = 5
_IN_FLIGHT = (events_module.STAGE_SPEC, events_module.STAGE_BUILDING)

_TASK_PARTS = ((TASK_STATUS_IDEA, "Ideas"), (TASK_STATUS_ARMED, "Armed"),
               (TASK_STATUS_RUNNING, "Running"), (TASK_STATUS_DONE, "Done"),
               (TASK_STATUS_FAILED, "Failed"))
_FEATURE_PARTS = ((events_module.STAGE_SPEC, "Spec"),
                  (events_module.STAGE_BUILDING, "Building"),
                  (events_module.STAGE_SHIPPED, "Shipped"),
                  (events_module.STAGE_ESCAPED, "Escaped"))
_HIGHLIGHTED = {TASK_STATUS_FAILED, events_module.STAGE_ESCAPED, "triage"}


def _stage(label: str, parts: list[tuple[str, str, int]]) -> dict:
    return {"label": label, "total": sum(n for _, _, n in parts),
            "parts": [{"key": key, "label": part_label, "n": n,
                       "highlighted": key in _HIGHLIGHTED}
                      for key, part_label, n in parts]}


def _project_args(scope) -> tuple:
    """`(project_id, unassigned)` — the pair every feed filter speaks."""
    return counts_module.project_of(scope), scope == SCOPE_NONE


def _task_scoped(stmt, scope):
    project_id, unassigned = _project_args(scope)
    if unassigned:
        return stmt.where(AgentTask.project_id.is_(None))
    if project_id is not None:
        return stmt.where(ws_scope.project_clause(AgentTask.project_id,
                                                  project_id))
    return stmt


def _tasks_stage(session, scope) -> dict:
    # Built on the Tasks tab's own predicate, so the parts sum to its count.
    by_status = dict(session.exec(task_query.population_stmt(
        select(AgentTask.status, func.count(AgentTask.id)),
        *_project_args(scope)).group_by(AgentTask.status)).all())
    return _stage("Tasks", [(key, label, int(by_status.get(key, 0)))
                            for key, label in _TASK_PARTS])


def _features_stage(scope) -> dict:
    project_id, unassigned = _project_args(scope)
    found = feature_index.stage_counts({
        **feature_index.NO_FILTERS, "project_id": project_id,
        "unassigned": unassigned})
    return _stage("Features", [(key, label, found[key])
                               for key, label in _FEATURE_PARTS])


def _defects_stage(session, scope) -> dict:
    queued = defect_store.in_queue()
    triage, open_rest, closed = (
        int(session.exec(counts_module.defects_stmt(scope).where(where)).one())
        for where in (queued,
                      (Defect.status == STATUS_OPEN) & ~queued,
                      Defect.status == STATUS_CLOSED))
    return _stage("Defects", [("triage", "Triage", triage),
                              ("open", "Open", open_rest),
                              ("closed", "Closed", closed)])


def _fix_tasks_carrying_defects(session, scope) -> int:
    """Fix tasks still to finish whose defect is still open — the loop's
    return edge, from Defects back into the Tasks queue."""
    stmt = (select(func.count(func.distinct(AgentTask.id)))
            .select_from(AgentTask)
            .join(Defect, Defect.fix_task_id == AgentTask.id)
            .where(AgentTask.kind == TASK_KIND_FIX,
                   AgentTask.status.in_(projects_module.OPEN_TASK_STATUSES),
                   Defect.status == STATUS_OPEN))
    return int(session.exec(_task_scoped(stmt, scope)).one())


def _loop(session, scope) -> dict:
    stages = [_features_stage(scope), _tasks_stage(session, scope),
              _defects_stage(session, scope)]
    fixes = _fix_tasks_carrying_defects(session, scope)
    return {"stages": stages, "fix_tasks": fixes}


def _run_next_stmt(stmt, scope):
    stmt = stmt.where(AgentTask.status.in_(
        (TASK_STATUS_ARMED, TASK_STATUS_QUEUED)))
    return _task_scoped(stmt, scope)


def _run_next_row(task: dict) -> dict:
    waits = task["waits_on"]
    action = ({"kind": "wait", "on": waits["id"], "on_status": waits["status"]}
              if waits else {"kind": task_rows.ACTION_RUN})
    keep = ("id", "title", "status", "priority", "kind", "feature_id",
            "feature_title", "step", "fixes_defects")
    return {**{k: task[k] for k in keep}, "action": action}


def _run_next(session, scope) -> dict:
    blocked_last = case((next_step.has_unmet_dependency(), 1), else_=0)
    stmt = (_run_next_stmt(select(AgentTask), scope)
            .order_by(blocked_last, AgentTask.priority_rank.asc(),
                      AgentTask.id.asc())
            .limit(RUN_NEXT_CAP))
    tasks = [task_store.serialize(t) for t in session.exec(stmt).all()]
    for enrich in (task_rows.feature_titles, task_rows.fix_defects,
                   task_rows.dependencies, task_rows.steps):
        enrich(tasks)
    total = session.exec(_run_next_stmt(
        select(func.count(AgentTask.id)), scope)).one()
    ready = session.exec(_run_next_stmt(
        select(func.count(AgentTask.id)), scope)
        .where(~next_step.has_unmet_dependency())).one()
    blocked = session.exec(_run_next_stmt(
        select(func.count(AgentTask.id)), scope)
        .where(next_step.has_unmet_dependency())).one()
    return {"total": int(total), "ready": int(ready), "blocked": int(blocked),
            "rows": [_run_next_row(t) for t in tasks]}


def _recently_done(session, scope) -> dict:
    """Done tasks of every grain that finished inside the week the board's
    `done_week` counts; `week` is that count, `rows` its newest five."""
    def week(stmt):
        return _task_scoped(stmt.where(
            next_step.done_since(next_step.DONE_WEEK)), scope)
    stmt = next_step.group_order(week(select(AgentTask)), next_step.GROUP_DONE)
    tasks = [task_store.serialize(t)
             for t in session.exec(stmt.limit(RECENTLY_DONE_CAP)).all()]
    for enrich in (task_rows.feature_titles, task_rows.fix_defects,
                   task_rows.steps):
        enrich(tasks)
    keep = ("id", "title", "status", "priority", "kind", "feature_id",
            "feature_title", "step", "fixes_defects")
    total = session.exec(week(select(func.count(AgentTask.id)))).one()
    return {"week": int(total),
            "rows": [{k: t[k] for k in keep} for t in tasks]}


def _running_stmt(stmt, scope):
    return _task_scoped(stmt.where(AgentTask.status == TASK_STATUS_RUNNING),
                        scope)


def _session_facts(session, trace_ids: list[str]) -> dict[str, dict]:
    """`{trace_id: {turns, cost_usd}}` — what a running row's meta reads.

    Turns are counted from `turn_usage`, not `sessions.prompts`: an autonomous
    run has one prompt and many turns."""
    if not trace_ids:
        return {}
    turns = dict(session.exec(
        select(TurnUsage.trace_id, func.count(TurnUsage.turn_uuid))
        .where(TurnUsage.trace_id.in_(trace_ids))
        .group_by(TurnUsage.trace_id)).all())
    cost = dict(session.exec(
        select(TraceSession.trace_id, TraceSession.cost_usd)
        .where(TraceSession.trace_id.in_(trace_ids))).all())
    return {t: {"turns": turns.get(t), "cost_usd": cost.get(t)}
            for t in trace_ids}


def _running_row(task: dict, facts: dict[str, dict]) -> dict:
    keep = ("id", "title", "status", "priority", "kind", "feature_id",
            "feature_title", "step", "fixes_defects", "live")
    trace_id = (task["live"] or {}).get("trace_id")
    return {**{k: task[k] for k in keep},
            **facts.get(trace_id, {"turns": None, "cost_usd": None})}


def _running(session, scope) -> dict:
    stmt = next_step.group_order(_running_stmt(select(AgentTask), scope),
                                 next_step.GROUP_RUNNING)
    tasks = [task_store.serialize(t)
             for t in session.exec(stmt.limit(RUNNING_CAP)).all()]
    for enrich in (task_rows.feature_titles, task_rows.fix_defects,
                   task_rows.live_runs, task_rows.steps):
        enrich(tasks)
    facts = _session_facts(session, [t["live"]["trace_id"] for t in tasks
                                     if t["live"] and t["live"]["trace_id"]])
    total = session.exec(_running_stmt(
        select(func.count(AgentTask.id)), scope)).one()
    return {"total": int(total),
            "rows": [_running_row(t, facts) for t in tasks]}


def _needs_decision(session, scope) -> dict:
    """The triage queue, under no project scope.

    A defect in the queue has no feature, so it has no project either: scoping
    it to one would empty the panel under every project. `all_projects` tells
    the surface that the scope it shows was not applied here.
    """
    queued = select(Defect).where(defect_store.in_queue())
    rows = session.exec(queued.order_by(Defect.opened_at.desc(),
                                        Defect.id.desc())
                        .limit(NEEDS_DECISION_CAP)).all()
    waiting = session.exec(select(func.count(Defect.id))
                           .where(defect_store.in_queue())).one()
    return {"waiting": int(waiting),
            "all_projects": isinstance(scope, (int, tuple)),
            "rows": [{"id": d.id, "round": d.round, "opened_at": d.opened_at,
                      "symptom": d.symptom,
                      "paths": (json.loads(d.suspect_paths_json)
                                if d.suspect_paths_json else [])}
                     for d in rows]}


def _feature_filters(scope, **extra) -> dict:
    project_id, unassigned = _project_args(scope)
    return {"project_id": project_id, "unassigned": unassigned, **extra}


def _features_in_flight(session, scope) -> dict:
    found, matched = feature_index.rows(
        _feature_filters(scope, stages=_IN_FLIGHT, sort="activity:desc"),
        size=FEATURES_IN_FLIGHT_CAP)
    filed = dict(session.exec(select(Feature.id, Feature.project_id).where(
        Feature.id.in_([row.id for row in found]))).all()) if found else {}
    return {"total": matched,
            "rows": [{"id": row.id, "title": row.title, "stage": row.stage,
                      "project_id": filed.get(row.id),
                      "last_activity_at": row.last_activity}
                     for row in found]}


def _project_head(scope: int) -> dict:
    summary = projects_module.summary_for(scope)
    live = summary.live_features
    done = summary.escaped + summary.by_stage.get(
        events_module.STAGE_SHIPPED, 0)
    return {"id": scope, "name": projects_module.label(summary),
            "breadcrumb": ["All projects", projects_module.label(summary)],
            "stage_bar": summary.stage_bar(),
            "shipped_pct": round(100 * done / live) if live else None,
            "open_tasks": summary.open_tasks,
            "open_defects": summary.defects_open,
            "spent_usd": feature_index.spent(scope)}


def _project_features(scope: int) -> dict:
    found, matched = feature_index.rows(
        _feature_filters(scope, sort="activity:desc"),
        size=PROJECT_FEATURES_CAP)
    return {"total": matched, "rows": [row.to_dict() for row in found]}


def _scope_label(scope) -> str:
    return str(ws_scope.echo(scope))


def overview(scope: int | tuple[int, ...] | str = SCOPE_ALL) -> dict:
    """Every Overview panel under `scope`.

    Raises `projects.ProjectNotFound` for a project id nothing is filed under,
    before any panel is read.
    """
    head = _project_head(scope) if isinstance(scope, int) else None
    with SessionLocal() as session:
        body = {"scope": _scope_label(scope), "loop": _loop(session, scope),
                "features_in_flight": _features_in_flight(session, scope),
                "running": _running(session, scope),
                "run_next": _run_next(session, scope),
                "recently_done": _recently_done(session, scope),
                "needs_decision": _needs_decision(session, scope)}
    if scope == SCOPE_ALL:
        body["projects"] = projects_module.panel()
    elif isinstance(scope, tuple) and scope:
        # An empty workspace omits the panel: its zero-total state is the
        # first-run "add a project" card, which is not what an empty
        # workspace means.
        body["projects"] = projects_module.panel(only=scope)
    if head is not None:
        body["project_head"] = head
        body["project_features"] = _project_features(scope)
    log.read("pipeline_overview_read", scope=_scope_label(scope))
    return body
