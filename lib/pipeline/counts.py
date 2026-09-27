"""All three Pipeline tab counts, from one read.

A count rendered only on the tab you are looking at is the one count that
carries no information. The shell therefore asks once, for all three, under
the scope it owns — and the scope is the only axis the three share.

The project axis is declared on two tables and derived on the third:
`agent_tasks.project_id` and `features.project_id` are columns, while a
defect's project is its feature's. An unattributed defect has no feature and
so has no project — the same first-class gap the ledger already carries for
blame, never a third state.
"""

from __future__ import annotations

from sqlmodel import func, select, text

from lib.orm import SessionLocal
from lib.orm.models.defects import Defect
from lib.orm.models.features import Feature
from lib.orm.models.tasks import AgentTask
from lib.tasks import query as task_query

SCOPE_ALL = "all"
SCOPE_NONE = "none"


class ScopeError(ValueError):
    """The `project` parameter named something that is not a scope."""


def parse_scope(raw: str | None) -> int | str:
    """`all` (default), `none`, or a repo id.

    Refused rather than silently widened: a dropped scope shows a list that
    does not match the selector above it.
    """
    value = (raw or "").strip()
    if not value or value == SCOPE_ALL:
        return SCOPE_ALL
    if value == SCOPE_NONE:
        return SCOPE_NONE
    try:
        return int(value)
    except ValueError:
        raise ScopeError(f"project must be 'all', 'none' or a repo id, "
                         f"got {value!r}") from None


def _scoped(stmt, column, scope):
    if scope == SCOPE_ALL:
        return stmt
    if scope == SCOPE_NONE:
        return stmt.where(column.is_(None))
    return stmt.where(column == scope)


def defects_stmt(scope):
    stmt = select(func.count(Defect.id)).select_from(Defect)
    if scope == SCOPE_ALL:
        return stmt
    stmt = stmt.outerjoin(Feature, Defect.feature_id == Feature.id)
    if scope == SCOPE_NONE:
        # An unattributed defect belongs to no project, so it belongs here —
        # dropping it would make it invisible under every scope.
        return stmt.where(Feature.project_id.is_(None))
    return stmt.where(Feature.project_id == scope)


def tasks_stmt(scope):
    """The Tasks tab's population: the same statement the list reports as
    `population`, so the strip and the toolbar count cannot disagree."""
    return task_query.population_stmt(
        select(func.count(AgentTask.id)),
        scope if isinstance(scope, int) else None, scope == SCOPE_NONE)


def features_stmt(scope):
    """Live features only: an abandoned one is never listed on the tab, so it
    is not counted on it either, and the four stage tabs sum to this."""
    from lib.features.index import LIVE_SQL  # noqa: PLC0415

    return _scoped(select(func.count(Feature.id)).where(text(LIVE_SQL)),
                   Feature.project_id, scope)


def counts(scope: int | str = SCOPE_ALL) -> dict[str, int]:
    with SessionLocal() as session:
        return {
            "tasks": session.exec(tasks_stmt(scope)).one(),
            "features": session.exec(features_stmt(scope)).one(),
            "defects": session.exec(defects_stmt(scope)).one(),
        }
