"""The task feed read by what each task needs next, and by status column.

Two readings of the same filtered population, both server-side so every
header count is the backlog's and never a loaded page's:

| Group   | Membership                                     | Order                        |
|---------|------------------------------------------------|------------------------------|
| ready   | armed, every dependency satisfied              | priority (urgent first), id  |
| running | running                                        | latest attempt started, desc |
| blocked | armed, some dependency unsatisfied             | priority (urgent first), id  |
| failed  | failed                                         | latest attempt ended, desc   |
| ideas   | idea                                           | priority (urgent first), id  |
| done    | done                                           | latest attempt ended, else last change, desc |

The six groups partition every unarchived task. The first five are the
unsettled ones and their counts sum to `open`; done is listed last, collapsed
to a preview, and never counted as open. A failed task is listed rather than hidden because it still wants a
decision — Retry, Re-arm or Mark done. A legacy `queued` row is read as armed;
nothing writes that status and no surface names it. "Satisfied" is `lib.tasks.deps`' per-edge-kind rule, stated once more
here as SQL so it can count and page; a dependency whose task row is gone
reads as unsatisfied, exactly as `deps.blocking` reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import aliased
from sqlmodel import func, select

from lib.orm.models.tasks import (
    AgentTask, AgentTaskDep, AgentTaskRun, DEP_KIND_AFTER, TASK_STATUS_ARMED,
    TASK_STATUS_ARCHIVED, TASK_STATUS_DONE, TASK_STATUS_FAILED,
    TASK_STATUS_IDEA, TASK_STATUS_QUEUED, TASK_STATUS_RUNNING,
)
from lib.tasks import query as task_query
from lib.tasks import rows as task_rows
from lib.tasks import store

GROUP_READY = "ready"
GROUP_RUNNING = "running"
GROUP_BLOCKED = "blocked"
GROUP_FAILED = "failed"
GROUP_IDEAS = "ideas"
GROUP_DONE = "done"
OPEN_GROUP_KEYS = (GROUP_READY, GROUP_RUNNING, GROUP_BLOCKED, GROUP_FAILED,
                   GROUP_IDEAS)
GROUP_KEYS = (*OPEN_GROUP_KEYS, GROUP_DONE)

GROUP_COPY = {
    GROUP_READY: ("Ready to run", "armed, dependencies clear"),
    GROUP_RUNNING: ("Running", ""),
    GROUP_BLOCKED: ("Blocked", "armed, waiting on another task"),
    GROUP_FAILED: ("Failed", "the last attempt failed — retry, re-arm or mark done"),
    GROUP_IDEAS: ("Ideas", "not armed — nothing runs until you arm it"),
    GROUP_DONE: ("Done", "newest finished first"),
}

#: Ideas open collapsed: the backlog of unarmed ideas is the longest group and
#: the one least likely to be acted on, so it previews and says how many more.
IDEAS_PREVIEW = 3
DONE_PREVIEW = 5
_PREVIEW = {GROUP_IDEAS: IDEAS_PREVIEW, GROUP_DONE: DONE_PREVIEW}

BOARD_COLUMNS = (TASK_STATUS_IDEA, TASK_STATUS_ARMED, TASK_STATUS_RUNNING,
                 TASK_STATUS_DONE)
#: Pageable like a column, never drawn as one: the board shows it as a count
#: behind Done's footer.
BOARD_PAGEABLE = (*BOARD_COLUMNS, TASK_STATUS_FAILED)

#: The "Queue is clear" banner's window: done tasks that finished this recently.
DONE_WEEK = timedelta(days=7)

_RUNNABLE = (TASK_STATUS_ARMED, TASK_STATUS_QUEUED)
_SETTLED = (TASK_STATUS_DONE, TASK_STATUS_FAILED)
_SETTLED_FOR_AFTER = (TASK_STATUS_DONE, TASK_STATUS_FAILED)


class UnknownGroup(ValueError):
    """A group or column name the feed does not have — a 400 naming it."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field


def has_unmet_dependency():
    """True for a task holding at least one dependency edge that has not
    released it — `lib.tasks.deps.unmet`, as a correlated EXISTS."""
    dep = aliased(AgentTask)
    unsatisfied = or_(
        dep.status.is_(None),
        and_(AgentTaskDep.kind == DEP_KIND_AFTER,
             dep.status.notin_(_SETTLED_FOR_AFTER)),
        and_(AgentTaskDep.kind != DEP_KIND_AFTER,
             dep.status != TASK_STATUS_DONE))
    return exists(
        select(AgentTaskDep.task_id)
        .select_from(AgentTaskDep)
        .outerjoin(dep, dep.id == AgentTaskDep.depends_on_id)
        .where(AgentTaskDep.task_id == AgentTask.id, unsatisfied))


def membership(group: str):
    """The WHERE clause that puts a task in `group`."""
    runnable = AgentTask.status.in_(_RUNNABLE)
    return {
        GROUP_READY: and_(runnable, ~has_unmet_dependency()),
        GROUP_RUNNING: AgentTask.status == TASK_STATUS_RUNNING,
        GROUP_BLOCKED: and_(runnable, has_unmet_dependency()),
        GROUP_FAILED: AgentTask.status == TASK_STATUS_FAILED,
        GROUP_IDEAS: AgentTask.status == TASK_STATUS_IDEA,
        GROUP_DONE: AgentTask.status == TASK_STATUS_DONE,
    }[group]


def _latest_start():
    return (select(func.max(AgentTaskRun.started_at))
            .where(AgentTaskRun.task_id == AgentTask.id)
            .correlate(AgentTask).scalar_subquery())


def _latest_end():
    return (select(func.max(AgentTaskRun.ended_at))
            .where(AgentTaskRun.task_id == AgentTask.id)
            .correlate(AgentTask).scalar_subquery())


def finished_at():
    """When a task finished: its latest attempt's end, or — having none — its
    row's last change."""
    return func.coalesce(_latest_end(), AgentTask.updated_at)


def _by_priority(stmt):
    return stmt.order_by(AgentTask.priority_rank.asc(), AgentTask.id.asc())


def _by_recency(stmt, column):
    return stmt.order_by(column.is_(None), column.desc(), AgentTask.id.desc())


def group_order(stmt, group: str):
    if group == GROUP_RUNNING:
        return _by_recency(stmt, _latest_start())
    if group == GROUP_FAILED:
        return _by_recency(stmt, _latest_end())
    if group == GROUP_DONE:
        return _by_recency(stmt, finished_at())
    return _by_priority(stmt)


def column_order(stmt, status: str):
    if status == TASK_STATUS_RUNNING:
        return _by_recency(stmt, _latest_start())
    if status in _SETTLED:
        return _by_recency(stmt, AgentTask.updated_at)
    return _by_priority(stmt)


def _status_blind(filters: dict) -> dict:
    """The feed's filters minus the status axis, which the groups and columns
    own. Archived stays out: it is reached by asking for it by name."""
    return {**filters, "status": "", "include_archived": False}


def _narrowed(stmt, filters: dict):
    return task_query.apply_filters(stmt, _status_blind(filters))


def _count(session, filters: dict, where) -> int:
    return int(session.exec(_narrowed(
        select(func.count(AgentTask.id)), filters).where(where)).one())


def _rows(session, stmt, *, limit: int, offset: int) -> list[dict]:
    tasks = session.exec(stmt.limit(limit).offset(offset)).all()
    return task_rows.next_step_facts([store.serialize(t) for t in tasks])


@dataclass(frozen=True)
class Window:
    """Which rows of a group or column to return: its page and size."""

    size: int
    page: int = 0

    @property
    def offset(self) -> int:
        return self.page * self.size


def _group(session, filters: dict, key: str, window: Window) -> dict:
    where = membership(key)
    n = _count(session, filters, where)
    stmt = group_order(_narrowed(select(AgentTask), filters).where(where), key)
    rows = _rows(session, stmt, limit=window.size, offset=window.offset)
    label, hint = GROUP_COPY[key]
    body = {"key": key, "label": label, "hint": hint, "n": n, "rows": rows,
            "page": window.page, "size": window.size,
            "more": max(0, n - window.offset - len(rows))}
    if key == GROUP_READY:
        body["bulk"] = n
    return body


def _window_for(key: str, only: Optional[str], size: int,
                page: int) -> Window:
    if only is not None:
        return Window(size=size, page=page)
    return Window(size=_PREVIEW.get(key, size))


def parse_only(raw, allowed: tuple[str, ...], field: str) -> Optional[str]:
    value = (raw or "").strip()
    if not value:
        return None
    if value not in allowed:
        raise UnknownGroup(f"unknown {field}: {value}", field)
    return value


def groups(session, filters: dict, *, size: int, page: int = 0,
           only: Optional[str] = None) -> dict:
    """The feed grouped by next step, under every non-status filter.

    With `only` naming a group, that group alone is returned at `page`; without
    it every group returns its first rows, Ideas previews three and Done five.
    """
    keys = (only,) if only else GROUP_KEYS
    out = [_group(session, filters, key, _window_for(key, only, size, page))
           for key in keys]
    counted = {g["key"]: g["n"] for g in out}
    open_n = sum(counted[key] if key in counted
                 else _count(session, filters, membership(key))
                 for key in OPEN_GROUP_KEYS)
    return {"groups": out, "open": open_n}


def _column(session, filters: dict, status: str, window: Window) -> dict:
    where = AgentTask.status == status
    n = _count(session, filters, where)
    stmt = column_order(_narrowed(select(AgentTask), filters).where(where),
                        status)
    cards = _rows(session, stmt, limit=window.size, offset=window.offset)
    body = {"status": status, "n": n, "cards": cards, "page": window.page,
            "size": window.size,
            "more": max(0, n - window.offset - len(cards))}
    if status == TASK_STATUS_ARMED:
        body["blocked"] = _count(session, filters,
                                 and_(where, has_unmet_dependency()))
    return body


def board(session, filters: dict, *, size: int, page: int = 0,
          only: Optional[str] = None) -> dict:
    """The feed as status columns, each paged on its own.

    Failed is not a column: it rides as a count (`failed`), and naming it as
    `only` pages its cards for the "show" expander.
    """
    statuses = (only,) if only else BOARD_COLUMNS
    columns = [_column(session, filters, status,
                       Window(size=size, page=page if only else 0))
               for status in statuses]
    failed = _count(session, filters, AgentTask.status == TASK_STATUS_FAILED)
    return {"columns": columns, "failed": failed,
            "done_week": _count(session, filters, done_since(DONE_WEEK))}


def done_since(window: timedelta):
    """Done tasks that finished (`finished_at`) inside `window`. The cutoff is
    written in the columns' own `%Y-%m-%d %H:%M:%S` UTC form so the comparison
    is textual."""
    cutoff = (datetime.now(timezone.utc) - window).strftime("%Y-%m-%d %H:%M:%S")
    return and_(AgentTask.status == TASK_STATUS_DONE, finished_at() >= cutoff)

