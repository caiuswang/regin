"""Who cites a feature: the ledger's "Referenced by" list.

Computed at read time from the bodies themselves, because a body is the only
record of what it says (I1). The `LIKE` is a prefilter only: it also matches a
citation inside code, an image, or a link with a title part, and the grammar
is what turns a hit into a reference (I4).
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.features import Feature
from lib.orm.models.tasks import TASK_STATUS_ARCHIVED, AgentTask
from lib.refs import resolve
from lib.refs.grammar import parse_refs

log = get_activity_logger("refs")


def _cites(body: str, ref: str) -> bool:
    return any(found.ref == ref for found in parse_refs(body))


def _citing_features(feature_id: int, needle: str, ref: str) -> list[dict]:
    with SessionLocal() as session:
        hits = session.exec(select(Feature.id, Feature.body).where(
            Feature.id != feature_id, Feature.body.like(needle))).all()
    ids = [fid for fid, body in hits if _cites(body, ref)]
    if not ids:
        return []
    return resolve.feature_items(resolve.feature_rows(
        " WHERE id IN :ids", {"ids": ids},
        order=" ORDER BY created_at DESC, id DESC"))


def _citing_tasks(feature_id: int, needle: str, ref: str) -> list[dict]:
    """Its own tasks are left out: the page already lists them."""
    with SessionLocal() as session:
        hits = session.exec(select(AgentTask).where(
            AgentTask.body.like(needle),
            AgentTask.status != TASK_STATUS_ARCHIVED,
            or_(AgentTask.feature_id.is_(None),
                AgentTask.feature_id != feature_id),
        ).order_by(AgentTask.created_at.desc(), AgentTask.id.desc())).all()
    return resolve.task_items([row for row in hits if _cites(row.body, ref)])


def referenced_by(feature_id: int) -> list[dict]:
    """Every other feature, then every non-archived task outside this feature,
    whose body references `feature:<feature_id>` outside code — each group
    newest first, as `RefItem`s."""
    ref = f"feature:{feature_id}"
    needle = f"%]({ref})%"
    items = (_citing_features(feature_id, needle, ref)
             + _citing_tasks(feature_id, needle, ref))
    log.read("backlinks_read", feature_id=feature_id, count=len(items))
    return items
