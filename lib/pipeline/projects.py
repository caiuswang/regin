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
from lib.orm.models.tasks import (
    AgentTask, TASK_STATUS_ARMED, TASK_STATUS_IDEA, TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
)
from lib.pipeline.counts import SCOPE_NONE
from lib.tasks import query as task_query
from lib.utils import pagination as offset
from lib.workspaces import scope as ws_scope

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

OPEN_TASK_STATUSES = (TASK_STATUS_IDEA, TASK_STATUS_ARMED, TASK_STATUS_QUEUED,
                      TASK_STATUS_RUNNING)

#: The Overview's Projects panel: this many rows, the rest summarised.
PANEL_ROWS = 5


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
    last_activity: Optional[str] = None

    @property
    def escaped(self) -> int:
        return self.by_stage.get(events_module.STAGE_ESCAPED, 0)

    @property
    def open_tasks(self) -> int:
        return sum(self.by_status.get(s, 0) for s in OPEN_TASK_STATUSES)

    @property
    def live_features(self) -> int:
        return sum(n for s, n in self.by_stage.items()
                   if s != events_module.STAGE_ABANDONED)

    def stage_bar(self) -> dict:
        return {s: self.by_stage.get(s, 0) for s in feature_index.LIVE_STAGES}

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
            "last_activity": self.last_activity,
        }


def _task_counts(session) -> dict[Optional[int], dict[str, int]]:
    # The Tasks tab's own population predicate, so a row here and the tab
    # strip under the same `?project=` count the same tasks.
    rows = session.exec(task_query.population_stmt(
        select(AgentTask.project_id, AgentTask.status, func.count(AgentTask.id)),
        None, False).group_by(AgentTask.project_id, AgentTask.status)).all()
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


def _latest(session) -> dict[Optional[int], str]:
    """The newest write under each project: a task touched, a defect touched,
    a feature declared. Read, not stored, like every other figure here."""
    out: dict[Optional[int], str] = {}
    reads = (
        select(AgentTask.project_id, func.max(AgentTask.updated_at))
        .group_by(AgentTask.project_id),
        select(Feature.project_id, func.max(Feature.created_at))
        .group_by(Feature.project_id),
        select(Feature.project_id, func.max(Defect.updated_at))
        .select_from(Defect)
        .outerjoin(Feature, Defect.feature_id == Feature.id)
        .group_by(Feature.project_id),
    )
    for stmt in reads:
        for project_id, when in session.exec(stmt).all():
            if when and str(when) > out.get(project_id, ""):
                out[project_id] = str(when)
    return out


def _collect() -> dict[Optional[int], Summary]:
    """Every scope that is registered or has a row, plus "No project"."""
    stages = feature_index.stage_counts_by_project()
    with SessionLocal() as session:
        repos = {r.id: r for r in session.exec(select(Repo)).all()}
        tasks = _task_counts(session)
        defects = _defect_counts(session)
        latest = _latest(session)

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
            defects_total=total, defects_open=open_count,
            last_activity=latest.get(pid))
    return out


def _work(summary: Summary) -> int:
    return sum(summary.by_stage.values()) + sum(summary.by_status.values())


STATUSES = (STATUS_EMPTY, STATUS_PLANNED, STATUS_ACTIVE, STATUS_COMPLETED)

#: Sort key -> (how a summary ranks under it, the direction it means by
#: default). A gap — no activity yet — sorts last in both directions.
SORT_KEYS = {
    "activity": (lambda s: s.last_activity or "", "desc"),
    "name": (lambda s: _label(s).lower(), "asc"),
    "progress": (lambda s: _progress(s), "desc"),
    "open_defects": (lambda s: s.defects_open, "desc"),
    # What the Overview's Projects panel leads with: a walked-back ship first,
    # then unresolved defects, then whatever moved most recently.
    "attention": (lambda s: (s.escaped, s.defects_open, s.last_activity or ""),
                  "desc"),
}
DEFAULT_SORT = "activity:desc"


class ProjectFilterRejected(ValueError):
    """An unknown facet value — a 400 naming the parameter."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field


def _progress(summary: Summary) -> float:
    d = summary.to_dict()["progress"]
    return d["done"] / d["live"] if d["live"] else -1.0


def parse_sort(raw) -> str:
    key, _, direction = (raw or "").strip().partition(":")
    key = key.strip().lower()
    if key not in SORT_KEYS:
        return DEFAULT_SORT
    direction = direction.strip().lower()
    return f"{key}:{direction if direction in ('asc', 'desc') else SORT_KEYS[key][1]}"


def parse_filters(args) -> dict:
    status = (args.get("status") or "").strip()
    if status and status not in STATUSES:
        raise ProjectFilterRejected(f"unknown status: {status}", "status")
    raw_project = (args.get("project") or "").strip()
    only = (ws_scope.member_ids(raw_project)
            if ws_scope.is_ws(raw_project) else None)
    return {"q": (args.get("q") or "").strip()[:200], "status": status,
            "sort": parse_sort(args.get("sort")), "only": only}


def _label(summary: Summary) -> str:
    """What the row renders and what search matches — one reading of a name."""
    if summary.name:
        return summary.name
    return ("No project" if summary.project_id is None
            else f"project #{summary.project_id}")


def _matches(summary: Summary, filters: dict) -> bool:
    if filters["status"] and status_of(summary.by_stage) != filters["status"]:
        return False
    term = filters["q"].lower()
    if not term:
        return True
    return term in f"{_label(summary)}\n{summary.description or ''}".lower()


def _sorted(rows: list[Summary], sort: str) -> list[Summary]:
    key, _, direction = sort.partition(":")
    rank = SORT_KEYS[key][0]
    # Gaps last whichever way the column runs: a project nothing has touched is
    # not the least recently touched.
    present = [s for s in rows if key != "activity" or s.last_activity]
    gaps = [s for s in rows if key == "activity" and not s.last_activity]
    present.sort(key=lambda s: (rank(s), -(s.project_id or 0)),
                 reverse=direction == "desc")
    return present + sorted(gaps, key=lambda s: (s.name or "").lower())


def listing(filters: dict, *, page: int, size: int) -> dict:
    """One page of projects, filtered and sorted here, never in the browser.

    The population is a handful of repos, so the rollup is built in full and
    narrowed in Python; what matters is that the narrowing is the server's.
    "No project" stays last under every sort: it is a bucket, not a project.
    """
    only = filters.get("only")
    collected = _members(_collect(), only)
    # A workspace is a set of repos; "No project" is in none of them.
    none = collected.pop(None, None)
    population = len(collected) + (none is not None)
    ordered = [*_sorted(list(collected.values()), filters["sort"]),
               *([none] if none is not None else [])]
    matched = [s for s in ordered if _matches(s, filters)]
    start = page * size
    items = [s.to_dict() for s in matched[start:start + size]]
    log.read("project_summaries_read", count=len(items), total=len(matched))
    envelope = offset.Page(
        items=items, total=len(matched), page=page, size=size,
        has_next=start + len(items) < len(matched),
        has_prev=page > 0).to_envelope()
    return offset.list_page(
        envelope, population=population, sort=filters["sort"],
        filters={"q": filters["q"], "status": filters["status"]})


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
    # The same population the panel's task counts and the Tasks tab read, so
    # the tile and the list under it describe the same tasks.
    stmt = task_query.population_stmt(
        select(AgentTask.id, AgentTask.title, AgentTask.status,
               AgentTask.priority, AgentTask.feature_id, AgentTask.updated_at),
        None, False)
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


def label(summary: Summary) -> str:
    return _label(summary)


def panel_row(summary: Summary) -> dict:
    """One Projects-panel row: the figures the row draws, nothing else."""
    return {"id": summary.project_id, "scope": summary.scope,
            "name": _label(summary), "status": status_of(summary.by_stage),
            "escaped": summary.escaped, "last_activity": summary.last_activity,
            "stage_bar": summary.stage_bar(),
            "features": summary.live_features,
            "open_tasks": summary.open_tasks,
            "open_defects": summary.defects_open}


def _quiet(summary: Summary) -> bool:
    """Nothing asks for a look: no walked-back ship, no open defect, no open
    task. A completed project is quiet too."""
    return not (summary.escaped or summary.defects_open or summary.open_tasks)


def _members(collected: dict, only: Optional[tuple[int, ...]]) -> dict:
    """Narrow to a workspace's member repos; `None` keeps every project."""
    if only is None:
        return collected
    return {pid: s for pid, s in collected.items() if pid in only}


def panel(limit: int = PANEL_ROWS,
          only: Optional[tuple[int, ...]] = None) -> dict:
    """The Overview's Projects panel under the attention sort.

    | project                        | goes to                                      |
    |--------------------------------|----------------------------------------------|
    | nothing open (incl. completed) | counted in `quiet`, never listed             |
    | anything else                  | counted in `active`; first `limit` in `rows` |

    "No project" is a bucket, not a project, and is not counted here.
    `only` narrows to a workspace's members.
    """
    collected = _members(_collect(), only)
    collected.pop(None, None)
    summaries = list(collected.values())
    active = _sorted([s for s in summaries if not _quiet(s)], "attention:desc")
    log.read("project_panel_read", total=len(summaries), active=len(active))
    return {"sort": "attention", "total": len(summaries),
            "active": len(active), "quiet": len(summaries) - len(active),
            "rows": [panel_row(s) for s in active[:limit]]}


def summary_for(scope: int | str) -> Summary:
    found = _collect().get(None if scope == SCOPE_NONE else scope)
    if found is None:
        raise ProjectNotFound(f"no project {scope!r}")
    return found
