"""Projects, the way Linear draws them: a list with progress, and one panel.

A project is a registered repo — the id `features.project_id` and
`agent_tasks.project_id` already carry — so nothing here is stored. Status and
progress are read off the stages of the features filed under a project, the
same way a feature's stage is read off its events: a declared project status
would be a second opinion that drifts from the rows it summarises.

Every figure is scoped exactly as `lib.pipeline.counts` scopes it, so a
project row and the tab strip under the same `?project=` never disagree:
tasks and features by their own column, a defect by its feature's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import func, select

from lib.activity_log import get_activity_logger
from lib.features import events as events_module
from lib.features import index as feature_index
from lib.orm import SessionLocal
from lib.orm.models import Repo
from lib.orm.models.defects import Defect
from lib.orm.models.features import Feature
from lib.orm.models.tasks import AgentTask, TASK_STATUS_ARCHIVED
from lib.pipeline.counts import SCOPE_NONE
from lib.utils import pagination as offset

log = get_activity_logger("features")

STATUS_EMPTY = "empty"
STATUS_PLANNED = "planned"
STATUS_ACTIVE = "in_progress"
STATUS_COMPLETED = "completed"

#: Stages whose done-claim counts toward progress. `escaped` is in: it was
#: claimed done, and the defects blamed on it are reported beside the bar,
#: not by un-counting the claim.
_DONE_STAGES = (events_module.STAGE_SHIPPED, events_module.STAGE_ESCAPED)

RECENT_LIMIT = 20


class ProjectNotFound(LookupError):
    """No registered repo has this id, and no row is filed under it."""


def status_of(by_stage: dict[str, int]) -> str:
    """The decision table, first matching row wins.

    | live features (not abandoned) | any `building` | any `spec` | status      |
    |-------------------------------|----------------|------------|-------------|
    | 0                             | —              | —          | empty       |
    | > 0                           | yes            | —          | in_progress |
    | > 0                           | no             | yes        | planned     |
    | > 0                           | no             | no         | completed   |
    """
    live = sum(n for s, n in by_stage.items()
               if s != events_module.STAGE_ABANDONED)
    if live == 0:
        return STATUS_EMPTY
    if by_stage.get(events_module.STAGE_BUILDING):
        return STATUS_ACTIVE
    if by_stage.get(events_module.STAGE_SPEC):
        return STATUS_PLANNED
    return STATUS_COMPLETED


@dataclass
class Summary:
    """One project's rollup. `project_id` None is the "No project" scope."""

    project_id: Optional[int]
    name: Optional[str] = None
    description: Optional[str] = None
    path: Optional[str] = None
    registered: bool = False
    by_stage: dict = field(default_factory=dict)
    by_status: dict = field(default_factory=dict)
    defects_total: int = 0
    defects_open: int = 0

    @property
    def scope(self) -> str:
        return SCOPE_NONE if self.project_id is None else str(self.project_id)

    def to_dict(self) -> dict:
        live = sum(n for s, n in self.by_stage.items()
                   if s != events_module.STAGE_ABANDONED)
        done = sum(self.by_stage.get(s, 0) for s in _DONE_STAGES)
        return {
            "id": self.project_id, "scope": self.scope, "name": self.name,
            "description": self.description, "path": self.path,
            "registered": self.registered,
            "status": status_of(self.by_stage),
            "progress": {"done": done, "live": live},
            "features": {"total": sum(self.by_stage.values()),
                         "by_stage": dict(self.by_stage)},
            "tasks": {"total": sum(self.by_status.values()),
                      "by_status": dict(self.by_status)},
            "defects": {"total": self.defects_total,
                        "open": self.defects_open},
        }


def _task_counts(session) -> dict[Optional[int], dict[str, int]]:
    rows = session.exec(
        select(AgentTask.project_id, AgentTask.status, func.count(AgentTask.id))
        .group_by(AgentTask.project_id, AgentTask.status)).all()
    out: dict[Optional[int], dict[str, int]] = {}
    for project_id, status, n in rows:
        out.setdefault(project_id, {})[status] = int(n)
    return out


def _defect_counts(session) -> dict[Optional[int], tuple[int, int]]:
    rows = session.exec(
        select(Feature.project_id, Defect.status, func.count(Defect.id))
        .select_from(Defect)
        .outerjoin(Feature, Defect.feature_id == Feature.id)
        .group_by(Feature.project_id, Defect.status)).all()
    out: dict[Optional[int], tuple[int, int]] = {}
    for project_id, status, n in rows:
        total, open_count = out.get(project_id, (0, 0))
        out[project_id] = (total + int(n),
                           open_count + (int(n) if status == "open" else 0))
    return out


def _collect() -> dict[Optional[int], Summary]:
    """Every scope that is registered or has a row, plus "No project"."""
    stages = feature_index.stage_counts_by_project()
    with SessionLocal() as session:
        repos = {r.id: r for r in session.exec(select(Repo)).all()}
        tasks = _task_counts(session)
        defects = _defect_counts(session)

    active = {rid for rid, r in repos.items() if r.is_active}
    ids = active | set(stages) | set(tasks) | set(defects)
    ids.discard(None)
    out: dict[Optional[int], Summary] = {}
    for pid in [*ids, None]:
        repo = repos.get(pid) if pid is not None else None
        total, open_count = defects.get(pid, (0, 0))
        out[pid] = Summary(
            project_id=pid,
            name=repo.name if repo else None,
            description=repo.description if repo else None,
            path=repo.path if repo else None,
            registered=pid in active,
            by_stage=stages.get(pid, {}), by_status=tasks.get(pid, {}),
            defects_total=total, defects_open=open_count)
    return out


def _work(summary: Summary) -> int:
    return sum(summary.by_stage.values()) + sum(summary.by_status.values())


def summaries() -> list[dict]:
    """Projects with work first, then by name; "No project" always last."""
    collected = _collect()
    none = collected.pop(None)
    ordered = sorted(collected.values(),
                     key=lambda s: (-_work(s), (s.name or "").lower(),
                                    s.project_id))
    log.read("project_summaries_read", count=len(ordered))
    return [s.to_dict() for s in [*ordered, none]]


def _scoped(stmt, column, scope):
    if scope == SCOPE_NONE:
        return stmt.where(column.is_(None))
    return stmt.where(column == scope)


def _open_defects(session, scope) -> list[dict]:
    stmt = (select(Defect.id, Defect.symptom, Defect.feature_id,
                   Feature.title, Defect.opened_at)
            .select_from(Defect)
            .outerjoin(Feature, Defect.feature_id == Feature.id)
            .where(Defect.status == "open"))
    stmt = _scoped(stmt, Feature.project_id, scope)
    rows = session.exec(stmt.order_by(Defect.opened_at.desc(), Defect.id.desc())
                        .limit(RECENT_LIMIT)).all()
    return [{"id": i, "symptom": symptom, "feature_id": fid,
             "feature_title": ftitle, "opened_at": opened}
            for i, symptom, fid, ftitle, opened in rows]


def _recent_tasks(session, scope) -> list[dict]:
    stmt = (select(AgentTask.id, AgentTask.title, AgentTask.status,
                   AgentTask.priority, AgentTask.feature_id,
                   AgentTask.updated_at)
            .where(AgentTask.status != TASK_STATUS_ARCHIVED))
    stmt = _scoped(stmt, AgentTask.project_id, scope)
    rows = session.exec(stmt.order_by(AgentTask.updated_at.desc(),
                                      AgentTask.id.desc())
                        .limit(RECENT_LIMIT)).all()
    return [{"id": i, "title": title, "status": status, "priority": priority,
             "feature_id": fid, "updated_at": updated}
            for i, title, status, priority, fid, updated in rows]


def _features(scope) -> dict:
    filters = ({"unassigned": True} if scope == SCOPE_NONE
               else {"project_id": scope})
    filters["sort"] = "stage"
    page = feature_index.page(filters=filters, size=offset.MAX_PAGE_SIZE)
    return {"items": [row.to_dict() for row in page.items],
            "matched": page.matched,
            "truncated": page.matched > len(page.items)}


def overview(scope: int | str) -> dict:
    """One project's panel: its rollup, its features, what is open in it."""
    summary = _collect().get(None if scope == SCOPE_NONE else scope)
    if summary is None:
        raise ProjectNotFound(f"no project {scope!r}")
    with SessionLocal() as session:
        defects = _open_defects(session, scope)
        tasks = _recent_tasks(session, scope)
    log.read("project_overview_read", scope=summary.scope)
    return {"project": summary.to_dict(), "features": _features(scope),
            "open_defects": defects, "recent_tasks": tasks}
