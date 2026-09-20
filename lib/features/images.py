"""Images attached to a feature — `feature_images`.

The storage, the ceilings and the `idx` discipline are `lib.attached_images`;
this binds them to the feature table.

What is specific to a feature is the delivery: these bytes are read through a
task's `feature_id` when an attempt is launched, never copied onto the slices
when the spec is transcribed. The mockup is the part of a spec most likely to
be replaced after the slices exist, and a copy would leave every slice already
cut carrying the superseded picture with nothing to say so.
"""

from __future__ import annotations

from typing import Optional

from lib.activity_log import get_activity_logger
from lib.attached_images import ImageSet, summarize
from lib.orm.models.features import FeatureImage

log = get_activity_logger("features")

_images = ImageSet(FeatureImage, "feature_id", "feature", log)

__all__ = ["attach", "get_bytes", "list_for", "payload_for", "remove",
           "summaries_for", "summarize"]


def attach(feature_id: int, data: bytes,
           want_idx: Optional[int] = None) -> dict:
    return _images.attach(feature_id, data, want_idx)


def list_for(feature_id: int) -> list[dict]:
    return _images.list_for(feature_id)


def summaries_for(feature_ids) -> dict[int, list[dict]]:
    return _images.summaries_for(feature_ids)


def get_bytes(feature_id: int, idx: int) -> Optional[tuple[bytes, str]]:
    return _images.get_bytes(feature_id, idx)


def remove(feature_id: int, idx: int) -> bool:
    return _images.remove(feature_id, idx)


def payload_for(feature_id: int) -> list[dict]:
    return _images.payload_for(feature_id)
