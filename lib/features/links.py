"""Peer relations between features — `follows` and `related`, never a tree.

`follows(a, b)` reads "a is the next phase of b". `related` is symmetric, so
it is stored once as `(min, max)` and a caller never has to know which way
round it was written. The `follows` graph stays acyclic: a phase cannot come
after itself, however long the chain.
"""

from __future__ import annotations

from typing import Iterable, Optional

from sqlmodel import Session, select

from lib.activity_log import get_activity_logger
from lib.features import events as events_module
from lib.orm import SessionLocal
from lib.orm.models.features import (
    LINK_FOLLOWS, LINK_KINDS, LINK_RELATED, Feature, FeatureLink,
)

log = get_activity_logger("defects")


class LinkRefused(ValueError):
    """A link the R4 table refuses. `field` names the offending input."""

    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.field = field


def _stored_pair(a: int, b: int, kind: str) -> tuple[int, int]:
    return (min(a, b), max(a, b)) if kind == LINK_RELATED else (a, b)


def _check_kind(kind: str) -> None:
    if kind not in LINK_KINDS:
        raise LinkRefused(f"kind must be one of {', '.join(LINK_KINDS)}",
                          field="kind")


def _check_ids(session: Session, a, b) -> None:
    for key, value in (("feature_id", a), ("other_id", b)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise LinkRefused(f"{key} must be an integer", field=key)
    if a == b:
        raise LinkRefused("a feature cannot link to itself", field="other_id")
    for key, value in (("feature_id", a), ("other_id", b)):
        if session.get(Feature, value) is None:
            raise LinkRefused(f"no feature #{value}", field=key)


def _reaches(session: Session, start: int, goal: int) -> bool:
    """Whether `start` follows `goal`, directly or through earlier phases."""
    seen, frontier = {start}, [start]
    while frontier:
        earlier = session.exec(
            select(FeatureLink.other_id).where(
                FeatureLink.kind == LINK_FOLLOWS,
                FeatureLink.feature_id.in_(frontier))).all()
        if goal in earlier:
            return True
        frontier = [fid for fid in earlier if fid not in seen]
        seen.update(frontier)
    return False


def add(session: Session, a: int, b: int, kind: str) -> FeatureLink:
    """Write one link inside the caller's transaction; idempotent."""
    _check_kind(kind)
    _check_ids(session, a, b)
    left, right = _stored_pair(a, b, kind)
    existing = session.exec(select(FeatureLink).where(
        FeatureLink.feature_id == left, FeatureLink.other_id == right,
        FeatureLink.kind == kind)).first()
    if existing is not None:
        return existing
    if kind == LINK_FOLLOWS and _reaches(session, b, a):
        raise LinkRefused("would make a follows cycle", field="other_id")
    row = FeatureLink(feature_id=left, other_id=right, kind=kind)
    session.add(row)
    session.flush()
    return row


def link(a: int, b: int, kind: str) -> FeatureLink:
    with SessionLocal() as session:
        row = add(session, a, b, kind)
        session.commit()
        session.refresh(row)
    log.write("feature_linked", feature_id=a, other_id=b, kind=kind)
    return row


def unlink(a: int, b: int, kind: str) -> bool:
    """Remove one link. True when a row went; a missing row is not an error."""
    _check_kind(kind)
    left, right = _stored_pair(a, b, kind)
    with SessionLocal() as session:
        row = session.exec(select(FeatureLink).where(
            FeatureLink.feature_id == left, FeatureLink.other_id == right,
            FeatureLink.kind == kind)).first()
        if row is None:
            return False
        session.delete(row)
        session.commit()
    log.write("feature_unlinked", feature_id=a, other_id=b, kind=kind)
    return True


def _refs(session: Session, ids: Iterable[int]) -> list[dict]:
    wanted = sorted(set(ids))
    if not wanted:
        return []
    rows = session.exec(select(Feature.id, Feature.title)
                        .where(Feature.id.in_(wanted))).all()
    return [{"id": fid, "title": title, "stage": events_module.stage(fid)}
            for fid, title in sorted(rows)]


def for_feature(feature_id: int) -> dict:
    """`{follows, followed_by, related}` — each a list of `{id, title, stage}`."""
    with SessionLocal() as session:
        rows = session.exec(select(FeatureLink).where(
            (FeatureLink.feature_id == feature_id)
            | (FeatureLink.other_id == feature_id))).all()
        follows = [r.other_id for r in rows
                   if r.kind == LINK_FOLLOWS and r.feature_id == feature_id]
        followed_by = [r.feature_id for r in rows
                       if r.kind == LINK_FOLLOWS and r.other_id == feature_id]
        related = [r.other_id if r.feature_id == feature_id else r.feature_id
                   for r in rows if r.kind == LINK_RELATED]
        return {"follows": _refs(session, follows),
                "followed_by": _refs(session, followed_by),
                "related": _refs(session, related)}


def follows_of(feature_ids: Iterable[int]) -> dict[int, list[int]]:
    """`{feature id: [ids it follows]}` for a page of rows, in one query."""
    wanted = list(set(feature_ids))
    out: dict[int, list[int]] = {fid: [] for fid in wanted}
    if not wanted:
        return out
    with SessionLocal() as session:
        rows = session.exec(
            select(FeatureLink.feature_id, FeatureLink.other_id).where(
                FeatureLink.kind == LINK_FOLLOWS,
                FeatureLink.feature_id.in_(wanted))
            .order_by(FeatureLink.other_id)).all()
    for fid, other in rows:
        out[fid].append(other)
    return out
