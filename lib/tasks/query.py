"""Filtering, sorting and counting for the task feed.

Every filter this module understands becomes a WHERE clause. None of them is
ever applied to a loaded page in the client: a filter over one 50-row page
answers a different question than the one the control claims to ask, and the
counts beside it would then describe the page rather than the backlog. That is
the whole reason `counts()` exists — the status chips report the true totals
*under the current non-status filters*, not `items.length`.

Sorting is likewise server-side and keyset-friendly: each option ends in
`id DESC` so the cursor has a unique tiebreaker, which is what keeps deep pages
O(log n) and immune to rows arriving while someone browses.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import func, select

from lib.orm.models.tasks import (
    AgentTask, TASK_PRIORITIES, TASK_STATUS_ARCHIVED, TASK_STATUSES,
)

Q_MAX = 200
TAG_MAX = 60

SORT_CREATED_DESC = "created_desc"
SORT_CREATED_ASC = "created_asc"
SORT_UPDATED_DESC = "updated_desc"
SORT_PRIORITY = "priority"
SORTS: tuple[str, ...] = (SORT_CREATED_DESC, SORT_CREATED_ASC,
                          SORT_UPDATED_DESC, SORT_PRIORITY)

GROUP_NONE = "none"
GROUP_STATUS = "status"
GROUP_PROJECT = "project"
GROUP_PRIORITY = "priority"
GROUPS: tuple[str, ...] = (GROUP_NONE, GROUP_STATUS, GROUP_PROJECT,
                           GROUP_PRIORITY)

# (column, direction) pairs handed to `keyset_page_stmt`. Every option ends in
# `id` so the cursor always has a unique tiebreaker, and every entry is a real
# COLUMN: `keyset_page_stmt` builds the next cursor by reading each order
# column's value back off the serialized row, which a computed expression has
# no key for.
ORDER_COLS: dict[str, list] = {
    SORT_CREATED_DESC: [(AgentTask.created_at, "DESC"), (AgentTask.id, "DESC")],
    SORT_CREATED_ASC: [(AgentTask.created_at, "ASC"), (AgentTask.id, "ASC")],
    SORT_UPDATED_DESC: [(AgentTask.updated_at, "DESC"), (AgentTask.id, "DESC")],
    SORT_PRIORITY: [(AgentTask.priority_rank, "ASC"),
                    (AgentTask.created_at, "DESC"), (AgentTask.id, "DESC")],
}


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
    raw_project = (args.get("project_id") or "").strip()
    unassigned = raw_project == "none"

    # `root_only` narrows the feed to the feature grain. Off by default and
    # deliberately so: a run is a task like any other, and hiding one from the
    # backlog by default would make the counts beside the feed describe
    # something other than the backlog.
    root_only = (args.get("root_only") or "").strip() in ("1", "true", "yes")

    return {
        "status": _enum(args, "status", TASK_STATUSES),
        "root_only": root_only,
        "parent_id": parse_int((args.get("parent_id") or "").strip() or None,
                               "parent_id"),
        "sort": _enum(args, "sort", SORTS, SORT_CREATED_DESC),
        "priority": _enum(args, "priority", TASK_PRIORITIES),
        "q": (args.get("q") or "").strip()[:Q_MAX],
        "tag": (args.get("tag") or "").strip()[:TAG_MAX],
        "source": (args.get("source") or "").strip(),
        "project_id": None if unassigned else parse_int(raw_project,
                                                           "project_id"),
        "unassigned": unassigned,
        "include_archived": (args.get("include_archived") or "").strip()
        in ("1", "true", "yes"),
    }


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
        stmt = stmt.where(AgentTask.priority == filters["priority"])
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
    """Narrow to one feature's runs, or to the features themselves.

    `parent_id` wins over `root_only` when both are given: naming a parent is
    the more specific request, and a query that asked for both would otherwise
    return nothing at all and look like an empty backlog.
    """
    if filters.get("parent_id") is not None:
        return stmt.where(AgentTask.parent_id == filters["parent_id"])
    if filters.get("root_only"):
        return stmt.where(AgentTask.parent_id.is_(None))
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
