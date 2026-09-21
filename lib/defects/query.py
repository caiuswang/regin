"""How a defect list is narrowed and ordered — one vocabulary, one parser.

The ledger used to answer "what order is this in?" differently per scope:
`all` came back `id DESC` while `unattributed`, `feature` and `fix-item` came
back `id ASC`, so pressing a scope pill silently reversed the list with no
header, no indicator and no control to put it back. The direction was never a
property of the *scope*; it is a property of the *sort*, and this module is
where that lives so no scope can hold a private opinion about it.

The two refusal postures differ on purpose, and the difference is which way a
wrong answer fails:

* an unknown **facet value** (a status) is **refused** — a silently dropped
  filter renders rows that do not match the controls sitting above them, which
  is a lie the reader has no way to detect;
* an unknown **sort key** falls back to the default — a sort is visible in the
  rows themselves, so a stale link should still show its data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from lib.orm.models.defects import STATUS_CLOSED, STATUS_OPEN
from lib.tasks.query import Q_MAX

SCOPE_ALL = "all"
SCOPE_UNATTRIBUTED = "unattributed"
SCOPE_TRIAGE = "triage"
SCOPE_FEATURE = "feature"
SCOPE_FIX_ITEM = "fix-item"

#: The three scopes a reader can *ask* for. `feature` and `fix-item` are scopes
#: you arrive in by following a key, so they are named by their parameter
#: rather than chosen by name.
#:
#: `triage` is narrower than `unattributed` and deliberately not a filter on
#: top of it: the queue is what is still *waiting to be routed*, so a declined
#: or snoozed defect has left it while remaining unattributed forever.
ASKABLE_SCOPES = (SCOPE_ALL, SCOPE_UNATTRIBUTED, SCOPE_TRIAGE)

STATUSES = (STATUS_OPEN, STATUS_CLOSED)
STATUS_ANY = "all"

SORT_OPENED = "opened"
SORT_UPDATED = "updated"
SORT_ROUND = "round"
SORT_STATUS = "status"
SORT_FEATURE = "feature"
SORTS = (SORT_OPENED, SORT_UPDATED, SORT_ROUND, SORT_STATUS, SORT_FEATURE)
DEFAULT_SORT = SORT_OPENED

ASC = "asc"
DESC = "desc"
DIRECTIONS = (ASC, DESC)
DEFAULT_DIR = DESC

#: "in no project" — a scope of its own, never the absence of one.
PROJECT_NONE = "none"


class QueryError(ValueError):
    """A request whose controls and whose rows could not both be honoured."""


@dataclass(frozen=True)
class Sort:
    key: str = DEFAULT_SORT
    dir: str = DEFAULT_DIR

    def __str__(self) -> str:
        return f"{self.key}:{self.dir}"


@dataclass(frozen=True)
class DefectQuery:
    """Every narrowing one list read can carry, already validated."""

    feature_id: Optional[int] = None
    fix_task_id: Optional[int] = None
    unattributed: bool = False
    triage: bool = False
    status: Optional[str] = None
    q: str = ""
    project_id: Optional[int] = None
    project_none: bool = False
    sort: Sort = field(default_factory=Sort)

    @property
    def scope(self) -> str:
        """What to call this read. A key beats a pill: `fix_item` and
        `feature_id` are followed links, and naming the list after the pill
        the reader happened to leave set would mislabel it."""
        if self.fix_task_id is not None:
            return SCOPE_FIX_ITEM
        if self.feature_id is not None:
            return SCOPE_FEATURE
        if self.triage:
            return SCOPE_TRIAGE
        return SCOPE_UNATTRIBUTED if self.unattributed else SCOPE_ALL


def parse_sort(raw) -> Sort:
    """`key:dir`, defaulted on both halves."""
    key, _, direction = (raw or "").strip().partition(":")
    if key not in SORTS:
        return Sort()
    return Sort(key, direction if direction in DIRECTIONS else DEFAULT_DIR)


def parse_status(raw) -> Optional[str]:
    value = (raw or "").strip()
    if not value or value == STATUS_ANY:
        return None
    if value not in STATUSES:
        raise QueryError(f"unknown status: {value}")
    return value


def parse_project(raw) -> tuple[Optional[int], bool]:
    """`(project_id, in_no_project)`."""
    value = (raw or "").strip()
    if not value:
        return None, False
    if value == PROJECT_NONE:
        return None, True
    try:
        return int(value), False
    except ValueError:
        raise QueryError(f"unknown project: {value}") from None


def row_id(raw) -> Optional[int]:
    """A parameter that names a row, or None.

    A non-integer is the same answer as absent — both mean "no such scope was
    asked for" — because a 400 here would make a typo louder than a wrong id.
    """
    try:
        return int((raw or "").strip())
    except (TypeError, ValueError):
        return None


def from_args(args) -> DefectQuery:
    """Build the query a request asked for, or refuse it."""
    scope = (args.get("scope") or SCOPE_ALL).strip()
    if scope not in ASKABLE_SCOPES:
        raise QueryError(f"unknown scope: {scope}")
    feature_id = row_id(args.get("feature_id"))
    fix_task_id = row_id(args.get("fix_item"))
    project_id, project_none = parse_project(args.get("project"))
    return DefectQuery(
        feature_id=feature_id,
        fix_task_id=fix_task_id,
        # A followed key outranks the pill, exactly as the scope name does:
        # `feature_id=5` and `feature_id IS NULL` together select nothing, and
        # an empty list is a worse answer than the one the link asked for.
        unattributed=(scope == SCOPE_UNATTRIBUTED
                      and feature_id is None and fix_task_id is None),
        triage=(scope == SCOPE_TRIAGE
                and feature_id is None and fix_task_id is None),
        status=parse_status(args.get("status")),
        q=(args.get("q") or "").strip()[:Q_MAX],
        project_id=project_id,
        project_none=project_none,
        sort=parse_sort(args.get("sort")))
