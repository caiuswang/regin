"""Escapes — defects blamed on a feature that had already shipped (regin:82).

**No two clocks are ever compared.** The obvious design — subtract a defect's
report moment from the feature's completion — has operands minted by two
different machines, so a defect near the ship boundary would fall on either
side of it according to host drift, and the metric would measure NTP quality as
much as rework.

So the question is answered where it is answerable without subtraction: at the
instant a human declares which feature is to blame, the feature's **current
last event** is pointed at, and the pointer is frozen onto the defect row. An
escape is `blamed_event.kind == 'shipped'` — one comparison against an
immutable row.

That pointer replaced a nullable `blamed_against_state` string, and with it the
two gap counters that string needed. A cached word could be absent (nobody
looked), stale (the tracker moved), or a vocabulary regin did not carry; an
event is none of those, because the row it names cannot change and the feature
always has one. So `counts_for` reports a count and no gaps.

The distortion this admits, stated so nobody rediscovers it as a bug: a defect
found while a feature was still building but blamed after it shipped counts as
an escape. Read the name as **blamed on an already-shipped feature**, which is
what it says.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.defects import Defect
from lib.orm.models.features import SHIPPED, FeatureEvent

log = get_activity_logger("defects")


def blame_pointer(feature_id: int) -> Optional[int]:
    """The event a blame assigned right now would name.

    None only for a feature with no events, which the creation transaction
    makes unreachable — every feature carries at least its `specced`.
    """
    with SessionLocal() as session:
        row = session.exec(
            select(FeatureEvent.id)
            .where(FeatureEvent.feature_id == feature_id)
            .order_by(FeatureEvent.id.desc())).first()
    return row


def is_escape(blamed_event_id: Optional[int]) -> bool:
    """Whether the event a defect was blamed against was a ship claim."""
    if blamed_event_id is None:
        return False
    with SessionLocal() as session:
        row = session.get(FeatureEvent, blamed_event_id)
    return row is not None and row.kind == SHIPPED


def against(event_id: int) -> int:
    """Defects blamed against one event — the stage's escape question, asked
    of the feature's current ship claim only."""
    with SessionLocal() as session:
        return len(session.exec(
            select(Defect.id).where(Defect.blamed_event_id == event_id)).all())


@dataclass(frozen=True)
class EscapeCounts:
    """Escapes for one feature.

    One field, where its predecessor had three. The two gap counters existed
    to hold the readings a cached string could have — never looked, and a word
    we do not carry — and a pointer at an append-only row has neither.
    """

    escapes: int = 0


def counts_for(feature_id: int) -> EscapeCounts:
    """Escapes for one feature — a join, not a string comparison.

    All-time and historical: closing an escape does not un-escape it, which is
    why this is a different figure from `open_defects` and both stay.
    """
    if feature_id is None:
        return EscapeCounts()
    with SessionLocal() as session:
        row = session.exec(
            select(Defect.id)
            .join(FeatureEvent, FeatureEvent.id == Defect.blamed_event_id)
            .where(Defect.feature_id == feature_id,
                   FeatureEvent.kind == SHIPPED)).all()
    log.read("defect_escapes_counted", feature_id=feature_id, count=len(row))
    return EscapeCounts(escapes=len(row))


def counts_by_feature() -> dict[int, int]:
    """`{feature_id: escapes}` for the whole population, in one statement.

    The index renders a row per feature, so a per-row `counts_for` would be
    one query per line — the shape `ledger.build()` measured at 726-1679 ms
    and the index exists to avoid.
    """
    with SessionLocal() as session:
        rows = session.exec(
            select(Defect.feature_id)
            .join(FeatureEvent, FeatureEvent.id == Defect.blamed_event_id)
            .where(Defect.feature_id.is_not(None),
                   FeatureEvent.kind == SHIPPED)).all()
    out: dict[int, int] = {}
    for feature_id in rows:
        out[feature_id] = out.get(feature_id, 0) + 1
    return out
