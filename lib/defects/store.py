"""The writer and reader of `defects`.

Every write lands here so the rules that make the count trustworthy hold
whichever caller did the write: recording an already-open defect is idempotent
rather than a second row, closing demands the answer to *what check would have
caught this*, attribution stays late-binding so a defect can wait unattributed
instead of being guessed onto the wrong feature, and the round only ever moves
through `reopen` — a declared act, never a re-typed string.

`feature_key` names **the feature that caused the bug**, which is not
necessarily the one the reporting session was working on. Nothing here
propagates a session's declared feature onto a defect.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.defects import (
    FEATURE_KEY_MAX, STATUS_CLOSED, STATUS_OPEN, SYMPTOM_MAX, Defect,
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


def normalize_key(feature_key: Optional[str]) -> Optional[str]:
    """The feature key as it will be stored, or None for unattributed.

    `feature_key` is identity — every reader joins on it by equality — so a
    padded or empty one is not a cosmetic flaw. `' CAI-206'` is a key
    `for_feature('CAI-206')` cannot find and a *second feature* in the index's
    population, and `''` is a row `unattributed()` does not return either, so
    it belongs to no listable scope at all. Normalizing at the one writer is
    what makes `attribute`'s existing strip a rule rather than that caller's
    habit.
    """
    stripped = (feature_key or "").strip()
    return stripped or None


def _check_key(feature_key: Optional[str]) -> None:
    """The cap the model declares for `feature_key`, enforced where the row is
    written. Declared and unenforced, it was a claim about the data nothing
    made true — the column is bare `TEXT`."""
    if feature_key and len(feature_key) > FEATURE_KEY_MAX:
        raise DefectError(
            f"a feature key is at most {FEATURE_KEY_MAX} characters")


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


def _existing(session, feature_key: str, key: str) -> Optional[Defect]:
    return session.exec(
        select(Defect).where(Defect.feature_key == feature_key,
                             Defect.symptom_key == key)).first()


def _existing_in_session(session, session_id: str,
                         key: str) -> Optional[Defect]:
    """The unattributed twin of `_existing`, matching the partial unique index.

    Scoped to one trace on purpose: across two traces the same wording really
    is two independent reports, and collapsing them would lose a real count.
    """
    return session.exec(
        select(Defect).where(Defect.feature_key.is_(None),
                             Defect.session_id == session_id,
                             Defect.symptom_key == key)).first()


def _paths(row: Defect) -> list[str]:
    return json.loads(row.suspect_paths_json) if row.suspect_paths_json else []


def _union_paths(row: Defect, extra: Iterable[str]) -> Optional[str]:
    merged = _paths(row) + [p for p in extra if p not in _paths(row)]
    return json.dumps(merged) if merged else None


def _bind_blame(row: Defect) -> None:
    """Freeze the tracker's state for the feature blamed, once and only once.

    A failed fetch writes NULL and the defect still lands: losing a report to a
    network blip corrupts the ledger the metric reads, which is worse than
    losing one field. See `lib/defects/escapes.py`.
    """
    if not row.feature_key or row.blamed_against_state is not None:
        return
    from lib.defects import escapes  # noqa: PLC0415  (circular at import time)
    row.blamed_against_state = escapes.blame_state(row.feature_key)


@dataclass(frozen=True)
class Recorded:
    """A defect plus what actually happened to it."""

    defect: Defect
    outcome: str

    @property
    def deduped(self) -> bool:
        return self.outcome == DEDUPED


def record_reporting(symptom: str, *, feature_key: Optional[str] = None,
                     session_id: Optional[str] = None,
                     suspect_paths: Iterable[str] = ()) -> Recorded:
    """Open a defect, or report the one already tracking this symptom.

    Dedup runs on whichever axis the row has: `(feature_key, symptom_key)` when
    attributed, `(session_id, symptom_key)` when not. A row with neither has
    nothing to collide with and is always distinct — that is a real record, not
    a defect in the record-keeping.

    A hit on a *closed* row refuses and points at `reopen`. Re-reporting a
    fixed bug is the definition of a walked-back completion, but auto-reopening
    would let the rework metric increment from a re-typed string.
    """
    if not symptom.strip():
        raise DefectError("a defect needs a symptom")
    feature_key = normalize_key(feature_key)
    _check_symptom(symptom)
    _check_key(feature_key)
    key = symptom_key(symptom)
    paths = list(suspect_paths)
    with SessionLocal() as session:
        found = None
        if feature_key is not None:
            found = _existing(session, feature_key, key)
        elif session_id:
            found = _existing_in_session(session, session_id, key)
        if found is not None:
            return Recorded(_dedup_hit(session, found, paths), DEDUPED)
        row = Defect(
            feature_key=feature_key, symptom=symptom.strip(), symptom_key=key,
            session_id=session_id, opened_at=_now(), updated_at=_now(),
            suspect_paths_json=json.dumps(paths) if paths else None)
        _bind_blame(row)
        session.add(row)
        session.commit()
        session.refresh(row)
        log.write("defect_opened", defect_id=row.id, feature_key=feature_key,
                  blamed_against_state=row.blamed_against_state)
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
             feature_key=found.feature_key)
    return found


def record(symptom: str, *, feature_key: Optional[str] = None,
           session_id: Optional[str] = None,
           suspect_paths: Iterable[str] = ()) -> Defect:
    """`record_reporting`, for callers that do not need the outcome."""
    return record_reporting(symptom, feature_key=feature_key,
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


def attribute(defect_id: int, feature_key: str) -> Defect:
    """Late-bind a defect to the feature that caused it — normally once blame
    on the fix commit names the origin, not at report time.

    A collision with a defect already carrying this symptom for this feature is
    refused rather than absorbed: two reports of one bug counted twice inflate
    defects-per-feature, the metric this ledger exists for. `merge` is the way
    out, because it is the operator asserting the two are the same.
    """
    key = normalize_key(feature_key)
    if key is None:
        raise DefectError("attribution needs a feature key")
    _check_key(key)
    with SessionLocal() as session:
        row = _get(session, defect_id)
        clash = _existing(session, key, row.symptom_key)
        if clash is not None and clash.id != row.id:
            raise DefectError(
                f"#{clash.id} already reports this symptom for {key} — "
                f"`regin defects merge {row.id} --into {clash.id}` if they "
                "are the same bug")
        # Re-attribution is a NEW blame against a DIFFERENT feature, so the
        # snapshot is re-taken. Write-once is per blame, not per row: keeping
        # the first feature's `state_category` would leave the escape verdict
        # describing a feature this defect is no longer blamed on — one
        # feature's escape counted against another's ledger.
        if row.feature_key and row.feature_key != key:
            row.blamed_against_state = None
        row.feature_key = key
        _bind_blame(row)
        row.updated_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        log.write("defect_attributed", defect_id=row.id,
                  feature_key=row.feature_key,
                  blamed_against_state=row.blamed_against_state)
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
        if (dup.fix_external_key and keeper.fix_external_key
                and (dup.fix_provider, dup.fix_external_key)
                != (keeper.fix_provider, keeper.fix_external_key)):
            raise DefectError(
                f"#{dup.id} and #{keeper.id} point at different fix items "
                f"({dup.fix_external_key} and {keeper.fix_external_key}) — "
                "somebody thought these were two bugs; detach one first")
        keeper.suspect_paths_json = _union_paths(keeper, _paths(dup))
        if not keeper.fix_external_key and dup.fix_external_key:
            keeper.fix_provider = dup.fix_provider
            keeper.fix_external_key = dup.fix_external_key
        keeper.updated_at = _now()
        session.delete(dup)
        session.add(keeper)
        session.commit()
        session.refresh(keeper)
        log.write("defect_merged", defect_id=duplicate_id, into=keeper.id,
                  symptom=dup.symptom)
        return keeper


def set_fix_item(defect_id: int, provider: str, external_key: str) -> Defect:
    """Point a defect at the tracker item that will close it.

    Last-writer-wins, unlike `session_features.is_primary`: that flag is a
    claim about the past that a later session could silently rewrite, while a
    fix item points at future work and legitimately moves. Nothing is counted
    from it, so a rewrite corrupts no metric.
    """
    if not (provider or "").strip() or not (external_key or "").strip():
        raise DefectError("a fix item needs a provider and a key")
    with SessionLocal() as session:
        row = _get(session, defect_id)
        row.fix_provider = provider.strip()
        row.fix_external_key = external_key.strip()
        row.updated_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        log.write("defect_fix_item_set", defect_id=row.id,
                  fix_external_key=row.fix_external_key)
        return row


def clear_fix_item(defect_id: int) -> Defect:
    with SessionLocal() as session:
        row = _get(session, defect_id)
        row.fix_provider = None
        row.fix_external_key = None
        row.updated_at = _now()
        session.add(row)
        session.commit()
        session.refresh(row)
        log.write("defect_fix_item_set", defect_id=row.id,
                  fix_external_key=None)
        return row


def by_fix_item(provider: str, external_key: str) -> list[Defect]:
    """Every defect one fix item is meant to close — the batch-fix query.

    There is deliberately no bulk-close verb: that is where the missing-check
    answer gets rubber-stamped.
    """
    with SessionLocal() as session:
        rows = session.exec(
            select(Defect).where(Defect.fix_provider == provider,
                                 Defect.fix_external_key == external_key)
            .order_by(Defect.id)).all()
        return list(rows)


def fix_states(rows: Iterable[Defect]) -> dict:
    """`{defect_id: state_category}` for each row carrying a fix item.

    Read on demand rather than cached: this is a CLI read over a handful of
    rows, so a cache would buy nothing and carry the staleness. An unreachable
    tracker yields `unknown` — never "in sync", which would be a claim about
    two axes that do not measure the same thing.
    """
    from lib.defects import escapes  # noqa: PLC0415  (circular at import time)
    from lib.tasks.integrations.base import STATE_UNKNOWN  # noqa: PLC0415
    out: dict = {}
    seen: dict = {}
    for row in rows:
        if not row.fix_external_key:
            continue
        key = row.fix_external_key
        if key not in seen:
            seen[key] = escapes.blame_state(key) or STATE_UNKNOWN
        out[row.id] = seen[key]
    return out


def for_feature(feature_key: str) -> list[Defect]:
    with SessionLocal() as session:
        rows = session.exec(
            select(Defect).where(Defect.feature_key == feature_key)
            .order_by(Defect.id)).all()
        log.read("defects_listed", feature_key=feature_key, count=len(rows))
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
            .where(Defect.session_id == session_id,
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
    with SessionLocal() as session:
        rows = session.exec(
            select(Defect).where(Defect.feature_key.is_(None))
            .order_by(Defect.id)).all()
        return list(rows)
