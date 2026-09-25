"""Validate a capture payload into the fields `store.capture_task` accepts.

An unknown field is a loud rejection naming it, never a silent drop. A caller
trying to pass `cwd`, `model` or `permission_mode` must find out immediately:
silently ignoring an execution parameter is how a misconfigured integration
becomes a false sense of capability, and accepting one would turn capture into
remote code execution.
"""

from __future__ import annotations

import re
from typing import Optional

from lib.orm.models.tasks import (
    BODY_MAX, EXTERNAL_ID_MAX, SOURCE_MAX, TASK_PRIORITIES, TITLE_MAX,
)

# The capture surface keeps the old bound. It is third-party intake reachable
# by a machine key, and the reason to raise the *edit* bound — an operator
# writing a feature's spec into the body — does not apply to a webhook.
# It refuses rather than truncating, exactly as it always did.
CAPTURE_BODY_MAX = 8000

# `project_id`, `priority` and `feature_id` are labels and structure, not
# execution parameters: setting any of them still leaves the task inert, so
# they are safe on the capture surface a machine key reaches.
#
# `assignee` is deliberately absent. Claiming a task is an act an
# authenticated human performs about *themselves*, and capture is reachable by
# a machine key that is nobody — so it is edit-only.
ALLOWED_FIELDS = frozenset({
    "title", "body", "source", "external_id", "tags", "links",
    "project_id", "priority", "feature_id",
})

# An edit cannot change provenance: `source` and `external_id` are the replay
# identity of the row, and rewriting them would let one task impersonate
# another's redelivery.
UPDATE_FIELDS = frozenset({"title", "body", "tags", "status",
                           "project_id", "priority", "feature_id", "assignee"})

STATUS_MAX = 40

MAX_TAGS = 10
MAX_LINKS = 10
_TAG_MAX = 60
_LINK_LABEL_MAX = 200
_LINK_HREF_MAX = 2000

_SOURCE_RE = re.compile(r"^[a-z0-9_-]+$")


class CaptureRejected(ValueError):
    """A malformed capture body — a 400 naming the offending field."""

    def __init__(self, message: str, field: Optional[str] = None) -> None:
        super().__init__(message)
        self.field = field


def _reject_unknown_fields(payload: dict, allowed=ALLOWED_FIELDS) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise CaptureRejected(f"unknown field: {unknown[0]}", unknown[0])


def _string(payload: dict, key: str, limit: int, *, required: bool) -> Optional[str]:
    value = payload.get(key)
    if value is None or value == "":
        if required:
            raise CaptureRejected(f"{key} is required", key)
        return None
    if not isinstance(value, str):
        raise CaptureRejected(f"{key} must be a string", key)
    value = value.strip()
    if required and not value:
        raise CaptureRejected(f"{key} is required", key)
    if len(value) > limit:
        raise CaptureRejected(f"{key} exceeds {limit} characters", key)
    return value or None


def _source(payload: dict) -> str:
    value = _string(payload, "source", SOURCE_MAX, required=True)
    if not _SOURCE_RE.match(value):
        raise CaptureRejected(
            "source must match [a-z0-9_-]+", "source")
    return value


def _tags(payload: dict) -> list[str]:
    raw = payload.get("tags")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CaptureRejected("tags must be a list", "tags")
    if len(raw) > MAX_TAGS:
        raise CaptureRejected(f"tags exceeds {MAX_TAGS} items", "tags")
    out = []
    for tag in raw:
        if not isinstance(tag, str):
            raise CaptureRejected("tags must be a list of strings", "tags")
        cleaned = tag.strip()[:_TAG_MAX]
        if cleaned:
            out.append(cleaned)
    return out


def _link(item) -> dict:
    if not isinstance(item, dict):
        raise CaptureRejected("links must be a list of {label, href}", "links")
    href = item.get("href")
    if not isinstance(href, str) or not href.strip():
        raise CaptureRejected("each link needs an href", "links")
    label = item.get("label")
    label = label.strip() if isinstance(label, str) and label.strip() else href
    return {"label": label[:_LINK_LABEL_MAX], "href": href.strip()[:_LINK_HREF_MAX]}


def _links(payload: dict) -> list[dict]:
    raw = payload.get("links")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CaptureRejected("links must be a list", "links")
    if len(raw) > MAX_LINKS:
        raise CaptureRejected(f"links exceeds {MAX_LINKS} items", "links")
    return [_link(item) for item in raw]


def _project_id(payload: dict) -> Optional[int]:
    """An explicit `null` clears the project; an absent key is untouched.

    The two must stay distinguishable — "move this task to the backlog" and
    "leave the project alone" are different requests, and collapsing them is
    how an unrelated PATCH silently unfiles a task.
    """
    value = payload.get("project_id")
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise CaptureRejected("project_id must be an integer or null",
                              "project_id")
    return value


def _priority(payload: dict) -> str:
    value = payload.get("priority")
    if value is None:
        return "none"
    if value not in TASK_PRIORITIES:
        raise CaptureRejected(
            f"priority must be one of {', '.join(TASK_PRIORITIES)}",
            "priority")
    return value


def parse_capture(payload) -> dict:
    """Normalized capture fields, or `CaptureRejected` naming the field.

    `status` is absent by construction — a captured task is always an idea,
    and nothing in the request can say otherwise.
    """
    if not isinstance(payload, dict):
        raise CaptureRejected("body must be a JSON object")
    _reject_unknown_fields(payload)
    fields = {
        "title": _string(payload, "title", TITLE_MAX, required=True),
        "body": _string(payload, "body", CAPTURE_BODY_MAX, required=False),
        "source": _source(payload),
        "external_id": _string(payload, "external_id", EXTERNAL_ID_MAX,
                               required=False),
        "tags": _tags(payload),
        "links": _links(payload),
        "feature_id": _optional_id(payload, "feature_id"),
        "priority": _priority(payload),
    }
    # Only a stated project is passed on: an absent key is what lets the store
    # carry the feature's project, and `None` would read as "No project".
    if "project_id" in payload:
        fields["project_id"] = _project_id(payload)
    return fields


def _optional_id(payload: dict, key: str) -> Optional[int]:
    """An integer row id, or an explicit `null` that clears the field.

    The two stay distinguishable for the reason `_project_id` states: "detach
    this run" and "leave its parentage alone" are different requests, and the
    caller distinguishes them by whether the key is present at all.
    """
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise CaptureRejected(f"{key} must be an integer or null", key)
    return value


def parse_update(payload) -> dict:
    """Normalized edit fields — only the keys the request actually carried.

    An absent key means "unchanged", which is what lets the archive control
    send `{"status": …}` alone. Whether a status is *reachable* from the row's
    current one is the store's call, not this module's: the answer depends on
    the row.
    """
    if not isinstance(payload, dict):
        raise CaptureRejected("body must be a JSON object")
    _reject_unknown_fields(payload, UPDATE_FIELDS)
    readers = {
        "title": lambda p: _string(p, "title", TITLE_MAX, required=True),
        "body": lambda p: _string(p, "body", BODY_MAX, required=False),
        "tags": _tags,
        "status": lambda p: _string(p, "status", STATUS_MAX, required=True),
        "project_id": _project_id,
        "feature_id": lambda p: _optional_id(p, "feature_id"),
        "assignee": lambda p: _optional_id(p, "assignee"),
        "priority": _priority,
    }
    return {key: read(payload) for key, read in readers.items()
            if key in payload}
