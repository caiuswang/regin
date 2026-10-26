"""One defect as JSON — the shape every surface that hands a defect out uses.

The web blueprint and the `regin` MCP server both serve this, so a field added
here reaches both and neither can grow a private opinion about what a defect
row looks like.
"""

from __future__ import annotations

import json

from lib.defects import images as defect_images
from lib.defects import store


def view(row, feature_title, blamed_event_kind, images=None,
         fix_state=None, fix_drift=False) -> dict:
    return {
        "id": row.id, "feature_id": row.feature_id, "symptom": row.symptom,
        # The feature's own title, never a task's: the two answer different
        # questions and diverge, and no surface falls back from one to the
        # other.
        "feature_title": feature_title,
        "status": row.status, "round": row.round,
        # What blame was assigned against, read off the event it points at. An
        # escape is `blamed_event_kind == 'shipped'` — one comparison, and no
        # "we never looked" reading to render as a gap.
        "blamed_event_kind": blamed_event_kind,
        "missing_check": row.missing_check,
        "suspect_paths": json.loads(row.suspect_paths_json)
        if row.suspect_paths_json else [],
        "fix_task_id": row.fix_task_id,
        # The fix task's CURRENT status, and only when asked for: it costs a
        # second read, and a list that always carried it would pay for a
        # column most readers never open. `regin defects list --with-fix-state`
        # is the same read under the same name.
        "fix_state": fix_state,
        # Open while its fix task reads done/archived — always carried,
        # because it is the one fix-task fact a row cannot be read without.
        "fix_drift": fix_drift,
        # How this defect left the triage queue, or None while it is still in
        # it. Separate from `status` because a declined report and a fixed bug
        # are both closed, and only one of them was ever looked at.
        "triage_state": row.triage_state,
        "triage_reason": row.triage_reason,
        # The row's place on the triage axis as the queue predicate reads it,
        # so a surface never re-derives the snooze-watermark rule.
        "queue_position": store.queue_position(row),
        "opened_at": row.opened_at, "updated_at": row.updated_at,
        "closed_at": row.closed_at,
        # Metadata only — the bytes are a separate GET, so a page of 50
        # reports does not carry a megabyte of base64.
        "images": defect_images.list_for(row.id) if images is None
        else images,
    }


def serialize(row) -> dict:
    """One defect, for the response to a write.

    Two reads for one row, and deliberately not the shape the list uses: this
    is a single row the caller just changed, so there is no loop for the cost
    to multiply through. The list reads its two extra facts off the row. The
    fix task's status is always echoed here: a lone row's fix column reads a
    missing status as a fix task that no longer exists.
    """
    state = store.fix_states([row]).get(row.id)
    return view(row, feature_title(row), blamed_kind(row), fix_state=state,
                fix_drift=store.is_fix_drift(row, state))


def feature_title(row):
    if row.feature_id is None:
        return None
    from lib.features import store as feature_store  # noqa: PLC0415

    feature = feature_store.get(row.feature_id)
    return feature.title if feature else None


def blamed_kind(row):
    """The kind of the event this defect is blamed against, or None."""
    if row.blamed_event_id is None:
        return None
    from lib.orm import SessionLocal  # noqa: PLC0415
    from lib.orm.models.features import FeatureEvent  # noqa: PLC0415

    with SessionLocal() as session:
        event = session.get(FeatureEvent, row.blamed_event_id)
    return event.kind if event else None
