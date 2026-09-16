"""Images attached to a captured idea — `agent_task_images`.

The composer's problem here is the mirror of the bridge's. `BridgeAttachment`
holds bytes that have no prompt *yet* and are spent by the first one that
claims them; a task's images have a row to hang off from the moment the task
exists, and they are re-read by every attempt at it. So nothing is claimed,
nothing is swept, and `idx` is a plain per-task order rather than an
`[Image #N]` marker some text has to carry: a task's prompt is its title and
body, and the images ride the launch as content blocks beside it.

Validation is `attachments.sniff_media_type` — magic bytes, not the
`Content-Type` a paste event claimed — and the ceilings are the same
`prompt_image_max_bytes` / `prompt_images_max_count` settings the bridge
composer answers to, because these bytes end up in the same place: a model's
context.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from lib.activity_log import get_activity_logger
from lib.agent_bridge.attachments import sniff_media_type
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTaskImage

log = get_activity_logger("tasks")

# Attempts to win a free `idx` before giving up. Concurrency here is two
# browser tabs on one task, not a fleet.
_ALLOC_ATTEMPTS = 5


def summarize(row: AgentTaskImage) -> dict:
    """The metadata a page needs. Never the bytes — those are a separate GET,
    so a 50-row task list does not carry a megabyte of base64."""
    return {"id": row.id, "idx": row.idx, "media_type": row.media_type,
            "byte_size": row.byte_size}


def _limits() -> tuple[int, int]:
    from lib.settings import settings

    return settings.prompt_image_max_bytes, settings.prompt_images_max_count


def _reject_unusable(task_id: int, data: bytes) -> str:
    """The media type, or ValueError with a sentence the operator can read."""
    media_type = sniff_media_type(data)
    if media_type is None:
        raise ValueError("not a supported image (png, jpeg, gif or webp)")
    max_bytes, max_count = _limits()
    if len(data) > max_bytes:
        raise ValueError(f"image is larger than {max_bytes} bytes")
    if _count(task_id) >= max_count:
        raise ValueError(f"at most {max_count} images per task")
    return media_type


def _count(task_id: int) -> int:
    with SessionLocal() as session:
        return len(session.execute(
            select(AgentTaskImage.id)
            .where(AgentTaskImage.task_id == task_id)).scalars().all())


def attach(task_id: int, data: bytes) -> dict:
    """Record `data` against `task_id`. Raises ValueError with the reason."""
    media_type = _reject_unusable(task_id, data)
    row = _insert_with_next_idx(task_id, media_type, data)
    log.write("task_image_attached", task_id=task_id, idx=row["idx"],
              byte_size=row["byte_size"], media_type=media_type)
    return row


def _next_idx(session, task_id: int) -> int:
    used = session.execute(
        select(AgentTaskImage.idx).where(AgentTaskImage.task_id == task_id)
    ).scalars().all()
    return (max(used) + 1) if used else 1


def _insert_with_next_idx(task_id: int, media_type: str, data: bytes) -> dict:
    """Insert, taking the next free `idx` for this task.

    Reading the high-water mark and inserting are two statements and the edit
    sheet can be open in two tabs, so `UNIQUE(task_id, idx)` is the real
    allocator — this just loses the race gracefully instead of turning a second
    paste into a 500.
    """
    digest = hashlib.sha256(data).hexdigest()
    for _ in range(_ALLOC_ATTEMPTS):
        with SessionLocal() as session:
            row = AgentTaskImage(
                task_id=task_id, idx=_next_idx(session, task_id),
                media_type=media_type, bytes_=data, byte_size=len(data),
                sha256=digest)
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                continue
            session.refresh(row)
            return summarize(row)
    raise ValueError("too many images being attached at once — try again")


def list_for(task_id: int) -> list[dict]:
    """This task's images in delivery order."""
    return summaries_for([task_id]).get(task_id, [])


def summaries_for(task_ids) -> dict[int, list[dict]]:
    """`{task_id: [summary, …]}` for a whole page in one query.

    A per-row read would be an N+1 against a BLOB table — the bytes column is
    excluded here for the same reason.
    """
    ids = list(task_ids)
    if not ids:
        return {}
    with SessionLocal() as session:
        rows = session.execute(
            select(AgentTaskImage.id, AgentTaskImage.task_id,
                   AgentTaskImage.idx, AgentTaskImage.media_type,
                   AgentTaskImage.byte_size)
            .where(AgentTaskImage.task_id.in_(ids))
            .order_by(AgentTaskImage.task_id, AgentTaskImage.idx)
        ).all()
    out: dict[int, list[dict]] = {}
    for row in rows:
        out.setdefault(row.task_id, []).append(
            {"id": row.id, "idx": row.idx, "media_type": row.media_type,
             "byte_size": row.byte_size})
    return out


def get_bytes(task_id: int, idx: int) -> Optional[tuple[bytes, str]]:
    """`(bytes, media_type)` for one image, or None."""
    with SessionLocal() as session:
        row = session.execute(
            select(AgentTaskImage).where(AgentTaskImage.task_id == task_id,
                                         AgentTaskImage.idx == idx)
        ).scalars().first()
    return (row.bytes_, row.media_type) if row else None


def remove(task_id: int, idx: int) -> bool:
    """Drop one image. False when there was nothing at that index.

    The freed `idx` is not reused — `_next_idx` counts from the high-water
    mark — so a thumbnail the operator is looking at cannot silently become a
    different picture.
    """
    with SessionLocal() as session:
        result = session.execute(
            delete(AgentTaskImage).where(AgentTaskImage.task_id == task_id,
                                         AgentTaskImage.idx == idx))
        session.commit()
    removed = bool(result.rowcount)
    if removed:
        log.write("task_image_removed", task_id=task_id, idx=idx)
    return removed


def payload_for(task_id: int) -> list[dict]:
    """This task's images as `runner._query_payload` wants them.

    `{media_type, data_b64}` per entry, in `idx` order — the same shape the
    bridge composer's staged images are handed to a turn in, so one prompt
    path carries both.
    """
    with SessionLocal() as session:
        rows = session.execute(
            select(AgentTaskImage)
            .where(AgentTaskImage.task_id == task_id)
            .order_by(AgentTaskImage.idx)
        ).scalars().all()
    return [{"media_type": row.media_type,
             "data_b64": base64.b64encode(row.bytes_).decode("ascii")}
            for row in rows]
