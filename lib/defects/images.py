"""Screenshots attached to a defect — `defect_images`.

The storage, the ceilings and the `idx` discipline are `lib.attached_images`;
this binds them to the defect table.

What is specific to a defect is that nothing delivers these bytes to a model.
A task's images ride its attempts and a feature's ride every slice cut from
it; a defect's are evidence of what the reporter saw, read by whoever opens
the row. The fix task cut from the defect carries its own.
"""

from __future__ import annotations

from typing import Optional

from lib.activity_log import get_activity_logger
from lib.attached_images import ImageSet, summarize
from lib.orm.models.defects import Defect, DefectImage

log = get_activity_logger("defects")

_images = ImageSet(DefectImage, Defect, "defect_id", "defect", log)

__all__ = ["attach", "get_bytes", "list_for", "remove", "summaries_for",
           "summarize"]


def attach(defect_id: int, data: bytes,
           want_idx: Optional[int] = None) -> dict:
    return _images.attach(defect_id, data, want_idx)


def list_for(defect_id: int) -> list[dict]:
    return _images.list_for(defect_id)


def summaries_for(defect_ids) -> dict[int, list[dict]]:
    return _images.summaries_for(defect_ids)


def get_bytes(defect_id: int, idx: int) -> Optional[tuple[bytes, str]]:
    return _images.get_bytes(defect_id, idx)


def remove(defect_id: int, idx: int) -> bool:
    return _images.remove(defect_id, idx)
