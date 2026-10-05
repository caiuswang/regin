"""Who cites an item: the "Referenced by" list under a feature's spec, and on
a task, defect or session page.

Computed at read time from the bodies themselves, because a body is the only
record of what it says (I1). The `LIKE` is a prefilter only: it also matches a
citation inside code, an image, or a link with a title part, and the grammar
is what turns a hit into a reference (I4).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import or_
from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models.features import Feature
from lib.orm.models.tasks import TASK_STATUS_ARCHIVED, AgentTask
from lib.refs import resolve
from lib.refs.grammar import RefError, parse_ref_target, parse_refs

log = get_activity_logger("refs")


def _cites(body: str, ref: str) -> bool:
    return any(found.ref == ref for found in parse_refs(body))


def _citing_features(ref: str, own_feature: Optional[int]) -> list[dict]:
    with SessionLocal() as session:
        stmt = select(Feature.id, Feature.body).where(
            Feature.body.contains(f"]({ref})", autoescape=True))
        if own_feature is not None:
            stmt = stmt.where(Feature.id != own_feature)
        hits = session.exec(stmt).all()
    ids = [fid for fid, body in hits if _cites(body, ref)]
    if not ids:
        return []
    return resolve.feature_items(resolve.feature_rows(
        " WHERE id IN :ids", {"ids": ids},
        order=" ORDER BY created_at DESC, id DESC"))


def _citing_tasks(ref: str, own_feature: Optional[int],
                  own_task: Optional[int]) -> list[dict]:
    """A feature's own tasks are left out: its page already lists them."""
    with SessionLocal() as session:
        stmt = select(AgentTask).where(
            AgentTask.body.contains(f"]({ref})", autoescape=True),
            AgentTask.status != TASK_STATUS_ARCHIVED)
        if own_feature is not None:
            stmt = stmt.where(or_(AgentTask.feature_id.is_(None),
                                  AgentTask.feature_id != own_feature))
        if own_task is not None:
            stmt = stmt.where(AgentTask.id != own_task)
        hits = session.exec(stmt.order_by(
            AgentTask.created_at.desc(), AgentTask.id.desc())).all()
    return resolve.task_items([row for row in hits if _cites(row.body, ref)])


def referenced_by(ref: str) -> list[dict]:
    """Every feature, then every non-archived task, whose body references
    `ref` outside code — each group newest first, as `RefItem`s.

    An item never cites itself, and a feature's list leaves out its own
    tasks.
    """
    target = parse_ref_target(ref)
    if target is None:
        raise RefError(f"not a reference: {ref}", "ref")
    kind, key = target
    own_feature = int(key) if kind == "feature" else None
    own_task = int(key) if kind == "task" else None
    items = (_citing_features(ref, own_feature)
             + _citing_tasks(ref, own_feature, own_task))
    log.read("backlinks_read", ref=ref, count=len(items))
    return items
