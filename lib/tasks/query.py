"""Filtering, sorting and counting for the task feed.

Every filter this module understands becomes a WHERE clause. None of them is
ever applied to a loaded page in the client: a filter over one 50-row page
answers a different question than the one the control claims to ask, and the
counts beside it would then describe the page rather than the backlog. That is
the whole reason `counts()` exists — the status chips report the true totals
*under the current non-status filters*, not `items.length`.

Sorting is likewise server-side: every order ends in `id`, so offset pages
are a total order and a row never lands on two pages or on neither.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import func, select

from lib.orm.models.tasks import (
    AgentTask, TASK_PRIORITIES, TASK_STATUS_ARCHIVED, TASK_STATUSES,
)

Q_MAX = 200
TAG_MAX = 60

#: Sort key -> (its column, the direction it means by default). The URL says
#: `key:dir`; an unknown key, or the retired single-token form
#: (`created_desc`), falls back to the default and is echoed back as such, so
#: the toolbar renders what the server applied rather than what was asked for.
SORT_KEYS: dict[str, tuple] = {
    "created": (AgentTask.created_at, "desc"),
    "updated": (AgentTask.updated_at, "desc"),
    "priority": (AgentTask.priority_rank, "asc"),
}
DEFAULT_SORT_KEY = "created"
DEFAULT_SORT = f"{DEFAULT_SORT_KEY}:{SORT_KEYS[DEFAULT_SORT_KEY][1]}"

GROUP_NONE = "none"
GROUP_STATUS = "status"
GROUP_PROJECT = "project"
GROUP_PRIORITY = "priority"
#: Not a column: what each task needs next (`lib.tasks.next_step`).
GROUP_NEXT_STEP = "next_step"
GROUPS: tuple[str, ...] = (GROUP_NONE, GROUP_STATUS, GROUP_PROJECT,
                           GROUP_PRIORITY, GROUP_NEXT_STEP)

class FilterRejected(ValueError):
    """A filter value the feed cannot honour — a 400 naming the parameter."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field


def parse_int(raw, field: str) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise FilterRejected(f"{field} must be an integer", field) from None


def _enum_list(args, field: str, allowed) -> str:
    """A comma list of `allowed` values, canonicalised to `allowed`'s order so
    `high,urgent` and `urgent,high` are one query. Any unknown member is
    refused, never dropped."""
    wanted = {v.strip() for v in (args.get(field) or "").split(",")
              if v.strip()}
    unknown = sorted(wanted - set(allowed))
    if unknown:
        raise FilterRejected(f"unknown {field}: {unknown[0]}", field)
    return ",".join(v for v in allowed if v in wanted)


def _enum(args, field: str, allowed, default: str = "") -> str:
    value = (args.get(field) or default).strip()
    if value and value not in allowed:
        raise FilterRejected(f"unknown {field}: {value}", field)
    return value


def parse_filters(args) -> dict:
    """Read the feed's query parameters off a request `args` mapping.

    A bad *value* is refused rather than dropped — a silently ignored filter
    shows a list that does not match the controls above it. An absent one is
    simply "no constraint".
    """
    # `project_id=none` is a real filter, not an absent one: "tasks in no
    # project" is the backlog, and it has to be expressible.
    raw_project = _arg(args, "project_id")
    unassigned = raw_project == "none"

    return {
        "status": _enum(args, "status", TASK_STATUSES),
        # `root_only` narrows the feed to the tasks under no feature. Off by
        # default and deliberately so: hiding a feature's steps by default would
        # make the counts beside the feed describe something other than the backlog.
        "unfeatured": _flag(args, "root_only"),
        "feature_id": parse_int(_arg(args, "feature_id") or None, "feature_id"),
        # Who claimed it. A filter, never a write: claiming is an act the task
        # surface leaves to `PATCH /tasks/<id>`.
        "assignee": parse_int(_arg(args, "assignee") or None, "assignee"),
        "sort": parse_sort(args.get("sort")),
        "priority": _enum_list(args, "priority", TASK_PRIORITIES),
        "q": _arg(args, "q")[:Q_MAX],
        "tag": _arg(args, "tag")[:TAG_MAX],
        "source": _arg(args, "source"),
        "project_id": None if unassigned else parse_int(raw_project,
                                                           "project_id"),
        "unassigned": unassigned,
        "include_archived": _flag(args, "include_archived"),
    }


def _arg(args, field: str) -> str:
    return (args.get(field) or "").strip()


def _flag(args, field: str) -> bool:
    return _arg(args, field) in ("1", "true", "yes")


def parse_sort(raw) -> str:
    """`key:dir`, defaulted on both halves; never refused."""
    key, _, direction = (raw or "").strip().partition(":")
    key = key.strip().lower()
    if key not in SORT_KEYS:
        return DEFAULT_SORT
    direction = direction.strip().lower()
    if direction not in ("asc", "desc"):
        direction = SORT_KEYS[key][1]
    return f"{key}:{direction}"


def ordered(stmt, sort: str):
    """Apply `sort`, tie-broken by `id` in the same direction so the order is
    total and a row cannot land on two offset pages or on neither."""
    key, _, direction = sort.partition(":")
    column = SORT_KEYS[key][0]
    descending = direction == "desc"
    cols = [column.desc() if descending else column.asc()]
    if key == "priority":
        cols.append(AgentTask.created_at.desc())
    cols.append(AgentTask.id.desc() if descending else AgentTask.id.asc())
    return stmt.order_by(*cols)


def population_stmt(stmt, project_id: Optional[int], unassigned: bool):
    """The tab's unnarrowed population under one project scope.

    What `/tasks` shows with no control touched: archived is a world you ask
    for by name. A feature's steps and a defect's fix tasks are counted — they
    are most of the backlog, and the feed lists them by default. The shell's
    tab count and the list's `population` are both this statement, so the two
    numbers cannot disagree.
    """
    stmt = stmt.where(AgentTask.status != TASK_STATUS_ARCHIVED)
    if unassigned:
        return stmt.where(AgentTask.project_id.is_(None))
    if project_id is not None:
        return stmt.where(AgentTask.project_id == project_id)
    return stmt


def like_pattern(term: str) -> str:
    """Escape the LIKE wildcards so a literal `%` searches for a `%`."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def apply_filters(stmt, filters: dict, *, ignore_status: bool = False):
    """Narrow `stmt` by every filter that carries a value.

    `ignore_status` is what lets `counts()` reuse this: the per-status totals
    must be computed under every *other* filter, or each chip would report the
    size of its own already-filtered slice. It drops the archived rule too —
    the status axis owns BOTH status constraints, and leaving the archived
    exclusion in place is how the Archived chip reads 0 while asking for
    `status=archived` returns rows.
    """
    if ignore_status:
        pass
    elif filters["status"]:
        stmt = stmt.where(AgentTask.status == filters["status"])
    elif not filters["include_archived"]:
        # Archived is a separate world: it is reachable by asking for it by
        # name, never mixed into an unfiltered feed.
        stmt = stmt.where(AgentTask.status != TASK_STATUS_ARCHIVED)

    if filters["q"]:
        pattern = like_pattern(filters["q"])
        stmt = stmt.where(
            AgentTask.title.like(pattern, escape="\\")
            | AgentTask.body.like(pattern, escape="\\"))
    if filters["tag"]:
        # tags_json is a JSON array of strings; the quoted form is what stops
        # `"ui"` from matching a tag named `ui-polish`.
        stmt = stmt.where(AgentTask.tags_json.like(
            like_pattern(f'"{filters["tag"]}"'), escape="\\"))
    if filters["source"]:
        stmt = stmt.where(AgentTask.source == filters["source"])
    if filters["priority"]:
        stmt = stmt.where(
            AgentTask.priority.in_(filters["priority"].split(",")))
    if filters.get("assignee") is not None:
        stmt = stmt.where(AgentTask.assignee == filters["assignee"])
    return _by_grain(_by_project(stmt, filters), filters)


def _by_project(stmt, filters: dict):
    """`project_id=none` is a real filter — "tasks in no project" IS the
    backlog, and it has to be expressible."""
    if filters["unassigned"]:
        return stmt.where(AgentTask.project_id.is_(None))
    if filters["project_id"] is not None:
        return stmt.where(AgentTask.project_id == filters["project_id"])
    return stmt


def _by_grain(stmt, filters: dict):
    """Narrow to one feature's tasks, or to the tasks under no feature.

    `feature_id` wins over `unfeatured` when both are given: naming a feature
    is the more specific request, and a query that asked for both would
    otherwise return nothing at all and look like an empty backlog.
    """
    if filters.get("feature_id") is not None:
        return stmt.where(AgentTask.feature_id == filters["feature_id"])
    if filters.get("unfeatured"):
        return stmt.where(AgentTask.feature_id.is_(None))
    return stmt


def _grouped(session, column, filters: dict, *, ignore_status: bool) -> dict:
    stmt = apply_filters(
        select(column, func.count(AgentTask.id)),
        filters, ignore_status=ignore_status,
    ).group_by(column)
    return {key: count for key, count in session.exec(stmt).all()}


def counts(session, filters: dict) -> dict:
    """Group totals on every axis the list can group by, plus the overall total.

    One grouped query per axis rather than one per header, and every axis is
    blind to its *own* filter for the same reason: a header must report how
    many tasks it WOULD show, so counting it inside its own filter makes every
    header read the size of its own slice.

    `total` is the sum of the statuses the feed would actually show — status
    blind like the axis, but still subject to the archived rule, so it matches
    what the list pages through rather than counting rows the feed hides.
    """
    by_status = _grouped(session, AgentTask.status, filters, ignore_status=True)
    shown = {status: by_status.get(status, 0) for status in TASK_STATUSES}
    countable = shown if filters["include_archived"] else {
        status: n for status, n in shown.items()
        if status != TASK_STATUS_ARCHIVED}
    return {
        "by_status": shown,
        "by_priority": _grouped(session, AgentTask.priority,
                                {**filters, "priority": ""},
                                ignore_status=False),
        # Keyed by id as a string: JSON object keys are strings, and a client
        # reading `by_project[String(id)]` should not have to guess which.
        "by_project": {str(pid) if pid is not None else "none": count
                       for pid, count in _grouped(
                           session, AgentTask.project_id,
                           {**filters, "project_id": None, "unassigned": False},
                           ignore_status=False).items()},
        "total": sum(countable.values()),
    }
