"""The writer and reader of `defects`.

Every write lands here so the rules that make the count trustworthy hold
whichever caller did the write: recording an already-open defect is idempotent
rather than a second row, closing demands the answer to *what check would have
caught this*, attribution stays late-binding so a defect can wait unattributed
instead of being guessed onto the wrong feature, and the round only ever moves
through `reopen` — a declared act, never a re-typed string.

`feature_id` names **the feature that caused the bug**, which is not
necessarily the one the reporting session was working on. Nothing here
propagates a session's declared feature onto a defect, and `reported_by_trace`
carries the invariant in its own name: it records who reported it, never who
caused it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.defects import query as defect_query
from lib.orm import SessionLocal
from lib.orm.models.defects import (
    STATUS_CLOSED, STATUS_OPEN, SYMPTOM_MAX, Defect,
)
from lib.tasks.query import like_pattern
from lib.utils.pagination import (
    DEFAULT_PAGE_SIZE, Page, paginate_query_stmt,
)

log = get_activity_logger("defects")

_WHITESPACE = re.compile(r"\s+")

#: What `record` did, so a caller can say so. An agent that retries a failed
#: command must not read a silent no-op as a successful open.
OPENED = "opened"
DEDUPED = "deduped"


class DefectError(ValueError):
    """A write that would leave the ledger untrustworthy."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def symptom_key(symptom: str) -> str:
    """The dedup axis: case- and whitespace-insensitive, so the same complaint
    typed twice matches while the reporter's own wording is stored verbatim."""
    return _WHITESPACE.sub(" ", symptom).strip().lower()


def _check_symptom(symptom: str) -> None:
    if len(symptom.strip()) > SYMPTOM_MAX:
        raise DefectError(
            f"a symptom is at most {SYMPTOM_MAX} characters; this one is "
            f"{len(symptom.strip())}")


def _get(session, defect_id: int) -> Defect:
    row = session.get(Defect, defect_id)
    if row is None:
        raise DefectError(f"no defect {defect_id}")
    return row


def get(defect_id: int) -> Defect:
    with SessionLocal() as session:
        return _get(session, defect_id)


def _existing(session, feature_id: int, key: str) -> Optional[Defect]:
    return session.exec(
        select(Defect).where(Defect.feature_id == feature_id,
                             Defect.symptom_key == key)).first()


def _existing_in_session(session, trace_id: str,
                         key: str) -> Optional[Defect]:
    """The unattributed twin of `_existing`, matching the partial unique index.

    Scoped to one trace on purpose: across two traces the same wording really
    is two independent reports, and collapsing them would lose a real count.
    """
    return session.exec(
        select(Defect).where(Defect.feature_id.is_(None),
                             Defect.reported_by_trace == trace_id,
                             Defect.symptom_key == key)).first()


def _paths(row: Defect) -> list[str]:
    return json.loads(row.suspect_paths_json) if row.suspect_paths_json else []


def _union_paths(row: Defect, extra: Iterable[str]) -> Optional[str]:
    merged = _paths(row) + [p for p in extra if p not in _paths(row)]
    return json.dumps(merged) if merged else None


def _require_feature(feature_id: Optional[int]) -> None:
    """Refuse a feature that does not exist, rather than letting the foreign
    key surface as a 500.

    The padding and blank-string normalisation this replaced existed because
    the key was a free string — `' CAI-206'` was a second feature nobody could
    spell. An integer has no spelling to normalize, but it can still name
    nothing, and that is the one check left worth making at the writer.
    """
    if feature_id is None:
        return
    from lib.orm.models.features import Feature  # noqa: PLC0415

    with SessionLocal() as session:
        if session.get(Feature, feature_id) is None:
            raise DefectError(f"no feature {feature_id}")


def _bind_blame(row: Defect) -> None:
    """Point the defect at the feature's current last event, once per blame.

    No fetch, no failure mode, no gap to count: the event is a local row that
    already exists. Its predecessor asked a tracker for a state word, so a
    network blip decided whether an escape was countable — which is why the
    old column had two gap counters and this one has none.
    """
    if row.feature_id is None or row.blamed_event_id is not None:
        return
    from lib.defects import escapes  # noqa: PLC0415  (circular at import time)
    row.blamed_event_id = escapes.blame_pointer(row.feature_id)


@dataclass(frozen=True)
class Recorded:
    """A defect plus what actually happened to it."""

    defect: Defect
    outcome: str

    @property
    def deduped(self) -> bool:
        return self.outcome == DEDUPED


def record_reporting(symptom: str, *, feature_id: Optional[int] = None,
                     session_id: Optional[str] = None,
                     suspect_paths: Iterable[str] = ()) -> Recorded:
    """Open a defect, or report the one already tracking this symptom.

    Dedup runs on whichever axis the row has: `(feature_id, symptom_key)` when
    attributed, `(reported_by_trace, symptom_key)` when not. A row with neither has
    nothing to collide with and is always distinct — that is a real record, not
    a defect in the record-keeping.

    A hit on a *closed* row refuses and points at `reopen`. Re-reporting a
    fixed bug is the definition of a walked-back completion, but auto-reopening
    would let the rework metric increment from a re-typed string.
    """
    if not symptom.strip():
        raise DefectError("a defect needs a symptom")
    _check_symptom(symptom)
    _require_feature(feature_id)
    key = symptom_key(symptom)
    paths = list(suspect_paths)
    with SessionLocal() as session:
        found = None
        if feature_id is not None:
            found = _existing(session, feature_id, key)
        elif session_id:
            found = _existing_in_session(session, session_id, key)
        if found is not None:
            return Recorded(_dedup_hit(session, found, paths), DEDUPED)
        row = Defect(
            feature_id=feature_id, symptom=symptom.strip(), symptom_key=key,
            reported_by_trace=session_id, opened_at=_now(), updated_at=_now(),
            suspect_paths_json=json.dumps(paths) if paths else None)
        _bind_blame(row)
        session.add(row)
        session.commit()
        session.refresh(row)
        log.write("defect_opened", defect_id=row.id, feature_id=feature_id,
                  blamed_event_id=row.blamed_event_id)
        return Recorded(row, OPENED)


def _dedup_hit(session, found: Defect, paths: list[str]) -> Defect:
    if found.status == STATUS_CLOSED:
        raise DefectError(
            f"#{found.id} reports this symptom and was closed — "
            f"`regin defects reopen {found.id}` if it is back. Re-reporting "
            "must not move the round by itself.")
    found.suspect_paths_json = _union_paths(found, paths)
    found.updated_at = _now()
    session.add(found)
    session.commit()
    session.refresh(found)
    log.read("defect_deduped", defect_id=found.id,
             feature_id=found.feature_id)
    return found


def record(symptom: str, *, feature_id: Optional[int] = None,
           session_id: Optional[str] = None,
           suspect_paths: Iterable[str] = ()) -> Defect:
    """`record_reporting`, for callers that do not need the outcome."""
    return record_reporting(symptom, feature_id=feature_id,
                            session_id=session_id,
                            suspect_paths=suspect_paths).defect


def reopen(defect_id: int) -> Defect:
    """Record that we claimed this fixed and were wrong. This is the round.

    `missing_check` is cleared, buying the readable invariant *it is non-NULL
    if and only if the defect is closed*: reopening is itself the evidence the
    named check did not catch the bug, so leaving the string standing would
    leave the ledger holding a claim it has disproven.
    """
    with SessionLocal() as session:
        row = _get(session, defect_id)
        if row.status == STATUS_OPEN:
            raise DefectError(
                f"#{row.id} is already open — the round counts fix attempts "
                "that were walked back, and incrementing it here would record "
                "one that never happened")
        row.round += 1
        row.status = STATUS_OPEN
        row.closed_at = None
        row.missing_check = None
        row.updated_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        log.write("defect_reopened", defect_id=row.id, round=row.round)
        return row


def close(defect_id: int, *, missing_check: str) -> Defect:
    """Close a defect, naming the check that would have caught it.

    The answer is mandatory because it is the only output of this ledger that
    changes future behaviour rather than describing past behaviour — and a
    closure with no answer is where that gets quietly skipped. Closing writes
    neither `feature_key` nor the fix-item pointer: attribution keeps one verb
    and one writer, and a bug being fixed says nothing about whether the work
    item scheduling the fix is still open.
    """
    if not missing_check.strip():
        raise DefectError(
            "closing a defect requires naming the check that would have "
            "caught it")
    with SessionLocal() as session:
        row = _get(session, defect_id)
        row.status = STATUS_CLOSED
        row.missing_check = missing_check.strip()
        row.closed_at = _now()
        row.updated_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        log.write("defect_closed", defect_id=row.id)
        return row


def attribute(defect_id: int, feature_id: int) -> Defect:
    """Late-bind a defect to the feature that caused it — normally once blame
    on the fix commit names the origin, not at report time.

    A collision with a defect already carrying this symptom for this feature is
    refused rather than absorbed: two reports of one bug counted twice inflate
    defects-per-feature, the metric this ledger exists for. `merge` is the way
    out, because it is the operator asserting the two are the same.
    """
    if feature_id is None:
        raise DefectError("attribution needs a feature")
    _require_feature(feature_id)
    with SessionLocal() as session:
        row = _get(session, defect_id)
        clash = _existing(session, feature_id, row.symptom_key)
        if clash is not None and clash.id != row.id:
            raise DefectError(
                f"#{clash.id} already reports this symptom for feature "
                f"{feature_id} — `regin defects merge {row.id} --into "
                f"{clash.id}` if they are the same bug")
        # Re-attribution is a NEW blame against a DIFFERENT feature, so the
        # pointer is re-taken. Write-once is per blame, not per row: keeping
        # the first feature's event would leave the escape verdict describing
        # a feature this defect is no longer blamed on — one feature's escape
        # counted against another's ledger.
        if row.feature_id is not None and row.feature_id != feature_id:
            row.blamed_event_id = None
        row.feature_id = feature_id
        _bind_blame(row)
        row.updated_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        log.write("defect_attributed", defect_id=row.id,
                  feature_id=row.feature_id,
                  blamed_event_id=row.blamed_event_id)
        return row


def merge(duplicate_id: int, *, into: int) -> Defect:
    """Fold a duplicate into a keeper and delete it.

    Deletion rather than closure: `close` demands a `missing_check`, so routing
    a duplicate through it means either fabricating one or writing "duplicate
    of #17" into the ledger's only behaviour-changing field — and the row would
    still count against the feature either way. The audit trail lives in the
    activity log, so no column is needed to remember this happened.

    No symptom or feature equality is required: the operator is asserting
    sameness, and two differently-worded reports of one bug is the case this
    is most needed for.
    """
    if duplicate_id == into:
        raise DefectError("a defect cannot be merged into itself")
    with SessionLocal() as session:
        dup = _get(session, duplicate_id)
        keeper = _get(session, into)
        if (dup.fix_task_id and keeper.fix_task_id
                and dup.fix_task_id != keeper.fix_task_id):
            raise DefectError(
                f"#{dup.id} and #{keeper.id} point at different fix items "
                f"(tasks {dup.fix_task_id} and {keeper.fix_task_id}) — "
                "somebody thought these were two bugs; detach one first")
        keeper.suspect_paths_json = _union_paths(keeper, _paths(dup))
        if not keeper.fix_task_id and dup.fix_task_id:
            keeper.fix_task_id = dup.fix_task_id
        keeper.updated_at = _now()
        session.delete(dup)
        session.add(keeper)
        session.commit()
        session.refresh(keeper)
        log.write("defect_merged", defect_id=duplicate_id, into=keeper.id,
                  symptom=dup.symptom)
        return keeper


def set_fix_item(defect_id: int, task_id: int) -> Defect:
    """Point a defect at the task that will close it.

    A foreign key rather than the `(provider, external_key)` pair it replaces:
    every item regin can point at is a row in `agent_tasks` now, so the loop
    closes in the schema and a reader can join it instead of re-parsing a
    string. The cost, stated: a defect can no longer be pointed at a remote
    issue that has no task here.

    Last-writer-wins, unlike `session_features.is_primary`: that flag is a
    claim about the past that a later session could silently rewrite, while a
    fix item points at future work and legitimately moves. Nothing is counted
    from it, so a rewrite corrupts no metric.
    """
    from lib.orm.models.tasks import AgentTask  # noqa: PLC0415

    with SessionLocal() as session:
        row = _get(session, defect_id)
        if session.get(AgentTask, task_id) is None:
            raise DefectError(f"no task {task_id}")
        row.fix_task_id = task_id
        row.updated_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        log.write("defect_fix_item_set", defect_id=row.id,
                  fix_task_id=row.fix_task_id)
        return row


def clear_fix_item(defect_id: int) -> Defect:
    with SessionLocal() as session:
        row = _get(session, defect_id)
        row.fix_task_id = None
        row.updated_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        log.write("defect_fix_item_set", defect_id=row.id, fix_task_id=None)
        return row


def by_fix_item(task_id: int) -> list[Defect]:
    """Every defect one task is meant to close — the batch-fix query.

    There is deliberately no bulk-close verb: that is where the missing-check
    answer gets rubber-stamped.
    """
    with SessionLocal() as session:
        rows = session.exec(
            select(Defect).where(Defect.fix_task_id == task_id)
            .order_by(Defect.id.desc())).all()
        return list(rows)


def fix_states(rows: Iterable[Defect]) -> dict:
    """`{defect_id: task status}` for each row carrying a fix item.

    A local read now, so there is no unreachable-tracker reading to report and
    no cache to go stale. A fix task somebody deleted yields nothing at all
    rather than "in sync", which would be a claim about two axes that do not
    measure the same thing.
    """
    from lib.orm.models.tasks import AgentTask  # noqa: PLC0415

    ids = {row.fix_task_id for row in rows if row.fix_task_id}
    if not ids:
        return {}
    with SessionLocal() as session:
        status = {task_id: session.get(AgentTask, task_id) for task_id in ids}
    return {row.id: status[row.fix_task_id].status
            for row in rows
            if row.fix_task_id and status.get(row.fix_task_id) is not None}


def for_feature(feature_id: int) -> list[Defect]:
    with SessionLocal() as session:
        rows = session.exec(
            select(Defect).where(Defect.feature_id == feature_id)
            .order_by(Defect.id.desc())).all()
        log.read("defects_listed", feature_id=feature_id, count=len(rows))
        return list(rows)


def latest_open_for_session(session_id: str) -> Optional[Defect]:
    """The defect this session is currently arguing about.

    Session locality is the strongest signal there is for *which defect is
    being discussed* — a "still not fixed" typed mid-session means the thing
    just worked on. It says nothing about *which feature is to blame*, and
    nothing here reads it that way.
    """
    with SessionLocal() as session:
        return session.exec(
            select(Defect)
            .where(Defect.reported_by_trace == session_id,
                   Defect.status == STATUS_OPEN)
            .order_by(Defect.id.desc())).first()


def every() -> list[Defect]:
    """The whole ledger, newest first.

    The scope that makes the other three a partition rather than a filter:
    without it, a defect attributed at the moment it was reported belonged to
    no listable scope, and reporting one from a page was a write nothing could
    read back. Newest first because this is the scope a reader opens to find
    the thing they just filed.
    """
    with SessionLocal() as session:
        rows = session.exec(
            select(Defect).order_by(Defect.id.desc())).all()
        log.read("defects_listed", scope="all", count=len(rows))
        return list(rows)


def unattributed() -> list[Defect]:
    """Newest first, the one direction every scope reads in.

    These four helpers used to disagree — `every()` descending, the other
    three ascending — so the CLI reversed its list on the same pill press the
    page did. The web list is `listing()` now; these stay for the terminal and
    for internal counts, and they hold the same order so the two surfaces
    cannot describe the ledger differently.
    """
    with SessionLocal() as session:
        rows = session.exec(
            select(Defect).where(Defect.feature_id.is_(None))
            .order_by(Defect.id.desc())).all()
        return list(rows)


# ── The list ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ListRow:
    """One line of a defect list, with the two facts a row renders that are
    not columns on `defects`.

    They arrive **on the row**, from the same statement, because the two ways
    of getting them here before were a session per row (`_blamed_kind`) and a
    store call per row (`feature_store.get`) — an unbounded query times an
    N+1. A figure that costs a query per row is a figure that stops being
    affordable exactly when the ledger gets interesting.
    """

    defect: Defect
    feature_title: Optional[str]
    blamed_event_kind: Optional[str]


def _sort_column(key: str):
    from lib.orm.models.features import Feature  # noqa: PLC0415

    return {
        defect_query.SORT_OPENED: Defect.opened_at,
        defect_query.SORT_UPDATED: Defect.updated_at,
        defect_query.SORT_ROUND: Defect.round,
        defect_query.SORT_STATUS: Defect.status,
        defect_query.SORT_FEATURE: Feature.title,
    }[key]


def _ordered(stmt, sort):
    """Apply the sort, tie-broken by `id`.

    The tiebreaker follows the sort's own direction rather than being pinned
    ascending: `opened_at` has second resolution, so a page of defects filed
    in the same second is ordered *entirely* by the tiebreaker, and a
    newest-first list whose ties read oldest-first is the reported bug in
    miniature. Either direction is a total order, which is what keeps a row
    from landing on two pages or on none.
    """
    descending = sort.dir == defect_query.DESC
    column = _sort_column(sort.key)
    return stmt.order_by(column.desc() if descending else column.asc(),
                         Defect.id.desc() if descending else Defect.id.asc())


def _by_project(stmt, query):
    from lib.orm.models.features import Feature  # noqa: PLC0415

    if query.project_id is not None:
        return stmt.where(Feature.project_id == query.project_id)
    if query.project_none:
        # The outer join already renders an unattributed defect's project as
        # NULL, which is the answer the ledger gives: it has none. One
        # predicate covers both halves of R4's `none` scope, so the two
        # cannot drift apart.
        return stmt.where(Feature.project_id.is_(None))
    return stmt


def _narrowed(stmt, query):
    if query.fix_task_id is not None:
        stmt = stmt.where(Defect.fix_task_id == query.fix_task_id)
    if query.feature_id is not None:
        stmt = stmt.where(Defect.feature_id == query.feature_id)
    if query.unattributed:
        stmt = stmt.where(Defect.feature_id.is_(None))
    if query.status:
        stmt = stmt.where(Defect.status == query.status)
    if query.q:
        stmt = stmt.where(Defect.symptom.like(like_pattern(query.q),
                                              escape="\\"))
    return _by_project(stmt, query)


def listing_stmt(query):
    """The one statement every defect list runs."""
    from lib.orm.models.features import Feature, FeatureEvent  # noqa: PLC0415

    stmt = (select(Defect, Feature.title, FeatureEvent.kind)
            .join(Feature, Feature.id == Defect.feature_id, isouter=True)
            .join(FeatureEvent, FeatureEvent.id == Defect.blamed_event_id,
                  isouter=True))
    return _ordered(_narrowed(stmt, query), query.sort)


def listing(query=None, *, page: int = 0,
            size: int = DEFAULT_PAGE_SIZE) -> Page:
    """One page of the ledger under `query`.

    Bounded, because the alternative was every row the table holds: `total`
    still describes the whole filtered population, so a surface can say
    "50 of 1 204" rather than implying it has shown everything.
    """
    query = query or defect_query.DefectQuery()
    with SessionLocal() as session:
        result = paginate_query_stmt(
            session, listing_stmt(query), page, size,
            row_to_dict=lambda row: ListRow(row[0], row[1], row[2]))
    log.read("defects_listed", scope=query.scope, count=len(result.items),
             total=result.total)
    return result
