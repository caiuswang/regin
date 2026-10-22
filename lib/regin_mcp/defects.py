"""Defect tools: report, read, list, edit, attribute, point at a fix task,
triage, and close.

A surface over `lib.defects.store` and `lib.defects.fix_items`, exactly as
`/api/defects` is, and every row it hands out is `lib.defects.view.serialize`
— the shape the web serves.
"""

from __future__ import annotations

from typing import Literal, Optional

from lib.regin_mcp import _common, images

TriageAction = Literal["reopen", "decline", "restore", "snooze", "unsnooze",
                       "merge"]


def _view(row) -> dict:
    from lib.defects import view
    return view.serialize(row)


def _write(act):
    from lib.defects import store
    try:
        return _view(act())
    except store.DefectError as exc:
        raise _common.refused(exc) from None


def defect_open(symptom: str, feature_id: int = 0, session_id: str = "",
                paths: Optional[list[str]] = None,
                image_paths: Optional[list[str]] = None) -> dict:
    """Record a defect: something a feature got wrong.

    Re-reporting the same symptom returns the existing row with
    `deduped: true` rather than opening a second one.

    Args:
        symptom: What went wrong, in your words.
        feature_id: The feature that CAUSED the bug (not necessarily the one
            you are working on); 0 leaves it unattributed — prefer that over
            a guess.
        session_id: Your session's id, where this was reported.
        paths: Suspect files — a hint, not a claim.
        image_paths: Local screenshots to attach. They go to the defect's
            thumbnail strip; the symptom is not touched.
    """
    from lib.defects import store
    blobs = images.read_paths("defect", image_paths)
    result = store.record_reporting(symptom, feature_id=feature_id or None,
                                    session_id=session_id or None,
                                    suspect_paths=paths or ())
    images.attach_all("defect", result.defect.id, blobs)
    return {**_view(store.get(result.defect.id)), "deduped": result.deduped}


def _list_args(feature_id: int, whole_ledger: bool, status: str) -> dict:
    from lib.defects import query
    if feature_id:
        _common.feature(feature_id)
        return {"scope": query.SCOPE_ALL, "feature_id": str(feature_id),
                "status": status}
    scope = query.SCOPE_ALL if whole_ledger else query.SCOPE_UNATTRIBUTED
    return {"scope": scope, "status": status}


def defect_list(feature_id: int = 0, whole_ledger: bool = False,
                status: str = "", limit: int = 50) -> dict:
    """List defects newest first: one feature's, the whole ledger, or the
    unattributed ones.

    Args:
        feature_id: A feature's defects; 0 for no feature filter.
        whole_ledger: With no feature, the whole ledger; otherwise only the
            defects awaiting attribution.
        status: open or closed; empty for both.
        limit: Max rows.
    """
    from lib.defects import images as defect_images
    from lib.defects import query, store, view
    from lib.utils.pagination import clamp_size
    try:
        q = query.from_args(_list_args(feature_id, whole_ledger, status))
    except query.QueryError as exc:
        raise _common.refused(exc, "status") from None
    page = store.listing(q, size=clamp_size(limit))
    by_defect = defect_images.summaries_for(r.defect.id for r in page.items)
    states = store.fix_states([r.defect for r in page.items])
    return {"defects": [
        view.view(r.defect, r.feature_title, r.blamed_event_kind,
                  by_defect.get(r.defect.id, []),
                  fix_drift=store.is_fix_drift(r.defect,
                                               states.get(r.defect.id)))
        for r in page.items]}


def defect_get(defect_id: int) -> dict:
    """One defect in full, with its screenshot metadata (bytes via
    `image_get`).

    Args:
        defect_id: The defect's id.
    """
    return _view(_common.defect(defect_id))


def defect_update(defect_id: int, symptom: Optional[str] = None,
                  paths: Optional[list[str]] = None) -> dict:
    """Rewrite a defect's wording and/or its suspect paths, open or closed.

    Args:
        defect_id: The defect's id.
        symptom: The new wording.
        paths: The new suspect paths — they replace the old list.
    """
    from lib.defects import store
    _common.defect(defect_id)
    if symptom is None and paths is None:
        raise ValueError("send symptom, paths, or both")
    return _write(lambda: store.edit(defect_id, symptom=symptom,
                                     suspect_paths=paths))


def defect_attribute(defect_id: int, feature_id: int) -> dict:
    """Name the feature that caused the bug — a human's assertion, made
    once blame on the fix names the origin.

    Args:
        defect_id: The defect's id.
        feature_id: The feature that caused it.
    """
    from lib.defects import store
    _common.defect(defect_id)
    _common.feature(feature_id)
    return _write(lambda: store.attribute(defect_id, feature_id))


def _fix_mode(task_id, create: bool, remove: bool) -> str:
    named = [m for m, on in (("task_id", task_id is not None),
                             ("create", create), ("remove", remove)) if on]
    if len(named) != 1:
        raise ValueError("task_id, create, remove: name exactly one of them")
    return named[0]


def _point(ids: list[int], task_id: int) -> list[dict]:
    from lib.defects import store
    return [_write(lambda i=i: store.set_fix_item(i, task_id)) for i in ids]


def _unpoint(ids: list[int]) -> list[dict]:
    from lib.defects import store
    return [_write(lambda i=i: store.clear_fix_item(i)) for i in ids]


def _create(ids: list[int]) -> list[dict]:
    from lib.defects import fix_items, store
    if len(ids) == 1:
        return [_write(lambda: fix_items.create_for(ids[0]))]
    try:
        _, rows = fix_items.create_batch(ids)
    except fix_items.BatchRefused as exc:
        raise ValueError(f"{exc} (blocking: {exc.blocking})") from None
    except store.DefectError as exc:
        raise _common.refused(exc) from None
    return [_view(row) for row in rows]


def defect_set_fix_task(defect_ids: list[int], task_id: Optional[int] = None,
                        create: bool = False, remove: bool = False) -> dict:
    """Point defects at the task that will fix them, author that task, or
    unpoint them. Name exactly one of task_id / create / remove.

    Args:
        defect_ids: The defects (one or more).
        task_id: The existing task that fixes them.
        create: Author one fix task from the symptoms (the defects must be
            attributed to one feature).
        remove: Unpoint them; the task itself is untouched.
    """
    mode = _fix_mode(task_id, create, remove)
    if not defect_ids:
        raise ValueError("defect_ids: name at least one defect")
    for defect_id in defect_ids:
        _common.defect(defect_id)
    if mode == "task_id":
        rows = _point(defect_ids, task_id)
    elif mode == "create":
        rows = _create(defect_ids)
    else:
        rows = _unpoint(defect_ids)
    return {"task_id": rows[0]["fix_task_id"], "defects": rows}


def _triage_act(action: str, defect_id: int, into_id, reason):
    from lib.defects import store
    if action == "merge":
        if not into_id:
            raise ValueError("into_id: name the defect to keep")
        _common.defect(into_id)
        return lambda: store.merge(defect_id, into=into_id)
    if action == "decline":
        return lambda: store.decline(defect_id, reason=reason)
    return lambda: getattr(store, action)(defect_id)


def defect_triage(defect_id: int, action: TriageAction,
                  into_id: Optional[int] = None,
                  reason: Optional[str] = None) -> dict:
    """Move a defect on the triage axis.

    reopen: we claimed it fixed and were wrong (this is a round).
    decline: not a defect — out of triage, no feature, no check.
    restore: undo a decline. snooze / unsnooze: hold it out of the queue
    until something writes to it, or return it now. merge: fold it into
    `into_id`; the keeper comes back and this id is gone.

    Args:
        defect_id: The defect's id.
        action: reopen, decline, restore, snooze, unsnooze or merge.
        into_id: For merge, the defect to keep.
        reason: For decline, why (optional).
    """
    _common.defect(defect_id)
    return _write(_triage_act(action, defect_id, into_id, reason))


def defect_close(defect_id: int, check: str) -> dict:
    """Close a defect, naming the check that would have caught it.

    Args:
        defect_id: The defect's id.
        check: The test or check that would have caught this bug.
    """
    from lib.defects import store
    return _write(lambda: store.close(defect_id, missing_check=check))


TOOLS = (defect_open, defect_list, defect_get, defect_update,
         defect_attribute, defect_set_fix_task, defect_triage, defect_close)
