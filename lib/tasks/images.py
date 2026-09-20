"""Images attached to a captured idea — `agent_task_images`.

The storage, the ceilings and the `idx` discipline are `lib.attached_images`;
this binds them to the task table. What is specific to a task is only where the
bytes go: they are delivered with attempts at *this* task, whereas a feature's
travel with every slice cut from it.
"""

from __future__ import annotations

from typing import Optional

from lib.activity_log import get_activity_logger
from lib.attached_images import ImageSet, summarize
from lib.orm.models.tasks import AgentTaskImage

log = get_activity_logger("tasks")

_images = ImageSet(AgentTaskImage, "task_id", "task", log)

__all__ = ["attach", "get_bytes", "list_for", "payload_for", "remove",
           "summaries_for", "summarize"]


def attach(task_id: int, data: bytes,
           want_idx: Optional[int] = None) -> dict:
    return _images.attach(task_id, data, want_idx)


def list_for(task_id: int) -> list[dict]:
    return _images.list_for(task_id)


def summaries_for(task_ids) -> dict[int, list[dict]]:
    return _images.summaries_for(task_ids)


def get_bytes(task_id: int, idx: int) -> Optional[tuple[bytes, str]]:
    return _images.get_bytes(task_id, idx)


def remove(task_id: int, idx: int) -> bool:
    return _images.remove(task_id, idx)


def payload_for(task_id: int) -> list[dict]:
    return _images.payload_for(task_id)
