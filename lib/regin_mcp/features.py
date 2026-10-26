"""Feature tools: declare, read, edit, link, attribute, open steps, slice,
and walk a feature through its lifecycle.

Each tool calls the same `lib.features` / `lib.tasks.feature_slices` function
its `/api/features` route calls, so a write through MCP lands the row the web
would have.
"""

from __future__ import annotations

from typing import Literal, Optional

from lib.regin_mcp import _common, images

Verb = Literal["ship", "reopen", "abandon"]
LinkKind = Literal["follows", "related"]


def feature_list(project: str = "", stage: str = "", query: str = "",
                 limit: int = 50) -> dict:
    """List features with their stage, rounds, task and defect counts.

    Args:
        project: Registered project name to scope to; empty for all.
        stage: One of spec / building / shipped / escaped / abandoned; empty
            lists every live (non-abandoned) feature.
        query: Free-text match on the title.
        limit: Max rows.
    """
    from lib.features import events, index
    if stage and stage not in events.STAGES:
        raise ValueError(f"unknown stage {stage!r}. One of: {', '.join(events.STAGES)}")
    filters = {"project_id": _common.project_id(project), "stage": stage,
               "q": query, "sort": "activity:desc"}
    rows, matched = index.rows(filters, size=limit)
    return {"total": matched, "features": [r.to_dict() for r in rows]}


def feature_get(feature_id: int) -> dict:
    """One feature in full: stage, spec (the row's body), tasks, defects,
    image metadata (bytes via `image_get`) and links to other features.

    Args:
        feature_id: The feature's id.
    """
    from lib.defects import store as defect_store
    from lib.defects import view as defect_view
    from lib.features import events, links, store
    from lib.features import images as feature_images
    from lib.tasks.store import serialize
    row = _common.feature(feature_id)
    return {"id": row.id, "title": row.title, "stage": events.stage(row.id),
            "project": store.project_name(row.project_id),
            "branch": row.branch, "spec": row.body or "",
            "tasks": [{"id": t["id"], "title": t["title"], "status": t["status"]}
                      for t in map(serialize, store.tasks_of(row.id))],
            "defects": [defect_view.serialize(d)
                        for d in defect_store.for_feature(row.id)],
            "images": feature_images.list_for(row.id),
            "links": links.for_feature(row.id)}


def _rollback_feature_create(feature_id: int) -> None:
    """Remove a feature whose creation failed before it was fully handed back.

    A feature is normally abandoned, not deleted, but a row that never made it
    to the caller is not yet part of the ledger — keeping it would make a retry
    duplicate it (D311).
    """
    from sqlalchemy import delete
    from lib.orm import SessionLocal
    from lib.orm.models.features import Feature, FeatureEvent, FeatureImage
    with SessionLocal() as session:
        session.exec(delete(FeatureImage).where(
            FeatureImage.feature_id == feature_id))
        session.exec(delete(FeatureEvent).where(
            FeatureEvent.feature_id == feature_id))
        row = session.get(Feature, feature_id)
        if row is not None:
            session.delete(row)
        session.commit()


def feature_create(title: str, spec: str = "", project: str = "",
                   session_id: str = "",
                   image_paths: Optional[list[str]] = None) -> dict:
    """Declare a feature: the row and its `specced` event, in one transaction.

    Args:
        title: What is being delivered.
        spec: The spec — thin prose stating intent and acceptance, no file paths.
        project: Registered project name to file it under.
        session_id: Your session's id, recorded as the run that minted it.
        image_paths: Local image files to attach; each one's `![](image:N)`
            is appended to the spec.
    """
    from lib.features import events, store
    blobs = images.read_paths("feature", image_paths)
    row = store.create(title, body=spec or None, trace_id=session_id or None,
                       project_id=_common.project_id(project))
    try:
        attached = images.attach_all("feature", row.id, blobs)
    except Exception:
        _rollback_feature_create(row.id)
        raise
    return {"id": row.id, "title": row.title, "stage": events.stage(row.id),
            "images": attached}


def feature_set_spec(feature_id: int, spec: str) -> dict:
    """Replace a feature's spec. The spec lives on the row and nowhere else.

    Args:
        feature_id: The feature's id.
        spec: The full new spec; it replaces the old one, it is not appended.
    """
    from lib.features import store
    _common.feature(feature_id)
    if not spec.strip():
        raise ValueError("the spec given is empty — nothing written")
    from lib.specs.reader import problems_summary
    row = store.set_body(feature_id, spec)
    return {"id": row.id, "spec_chars": len(row.body or ""),
            "problems": problems_summary(row.body or "")}


def _set_fields(feature_id: int, title, project, branch) -> None:
    from lib.features import store
    if branch is not None:
        store.clean_branch(branch)
    if title is not None:
        store.rename(feature_id, title)
    if project is not None:
        store.set_project(feature_id, _common.project_id(project))
    if branch is not None:
        store.set_branch(feature_id, branch)


def feature_update(feature_id: int, title: Optional[str] = None,
                   project: Optional[str] = None,
                   branch: Optional[str] = None) -> dict:
    """Edit a feature's title, project or integration branch. An omitted
    field is left alone.

    Args:
        feature_id: The feature's id.
        title: The new title.
        project: Registered project name to move it to; "" unfiles it.
        branch: The integration branch; "" clears it.
    """
    from lib.features import store
    _common.feature(feature_id)
    try:
        _set_fields(feature_id, title, project, branch)
    except store.FeatureError as exc:
        raise _common.refused(exc) from None
    return feature_get(feature_id)


def feature_lifecycle(feature_id: int, action: Verb,
                      session_id: str = "") -> dict:
    """Ship, reopen or abandon a feature — the operator's declarations.

    Ship is the done-claim later defects are blamed against; reopen walks a
    shipped/escaped feature back (+1 round) or returns an abandoned one to
    spec; abandon takes it down (no round, no delete).

    Args:
        feature_id: The feature's id.
        action: ship, reopen or abandon.
        session_id: Your session's id, recorded on the event.
    """
    from lib.features import events, store
    from lib.orm.models.features import OPERATOR
    _common.feature(feature_id)
    verb = {"ship": store.ship, "reopen": store.reopen,
            "abandon": store.abandon}[action]
    try:
        event = verb(feature_id, declared_by=OPERATOR,
                     trace_id=session_id or None)
    except store.FeatureError as exc:
        raise _common.refused(exc) from None
    return {"id": feature_id, "stage": events.stage(feature_id),
            "event": {"id": event.id, "kind": event.kind,
                      "declared_by": event.declared_by,
                      "trace_id": event.trace_id}}


def feature_link(feature_id: int, other_id: int, kind: LinkKind,
                 remove: bool = False) -> dict:
    """Link this feature to another, or remove that link.

    Args:
        feature_id: This feature's id.
        other_id: The other feature's id.
        kind: follows (this one builds on the other) or related.
        remove: True removes the link; removing one never there succeeds.
    """
    from lib.features import links
    try:
        if remove:
            links.unlink(feature_id, other_id, kind)
        else:
            links.link(feature_id, other_id, kind)
    except links.LinkRefused as exc:
        raise _common.refused(exc) from None
    return {"links": links.for_feature(feature_id)}


def _unattribute(feature_id: int, session_id: str) -> dict:
    from lib.features import attribution, spans
    if not attribution.unattribute(feature_id, session_id):
        raise ValueError(attribution.no_declaration(feature_id, session_id))
    spans.unattributed(feature_id, attribution.canonical_trace(session_id))
    return {"feature_id": feature_id, "trace_id": session_id,
            "unattributed": True}


def feature_attribute(feature_id: int, session_id: str, remove: bool = False,
                      primary: bool = False) -> dict:
    """Declare that a session built this feature, or withdraw that.

    Args:
        feature_id: The feature's id.
        session_id: The session (trace id) being attributed.
        remove: True withdraws the declaration.
        primary: Make this the session's primary feature.
    """
    from lib.features import attribution, spans
    _common.feature(feature_id)
    session_id = (session_id or "").strip()
    if not session_id:
        raise ValueError("session_id: required")
    if remove:
        return _unattribute(feature_id, session_id)
    try:
        attribution.require_known_trace(session_id)
    except attribution.UnknownSession as exc:
        raise _common.refused(exc, "session_id") from None
    row = attribution.attribute(feature_id, session_id, primary=primary)
    spans.attributed(row)
    return {"feature_id": row.feature_id, "trace_id": row.trace_id,
            "provenance": row.provenance, "is_primary": bool(row.is_primary),
            "round": row.round}


def feature_open_step(feature_id: int, stage: str,
                      body: Optional[str] = None) -> dict:
    """Open the feature's step task for one stage, creating it on first use.

    A second call returns the same task (`created: false`) — another attempt
    is another run of that task, not another task.

    Args:
        feature_id: The feature's id.
        stage: A stage key of the feature's project (e.g. spec, build).
        body: The step's description, used only when it is created.
    """
    from lib.features import steps, store
    try:
        task, created = steps.open_step(feature_id, stage, body)
    except steps.StepRejected as exc:
        raise _common.refused(exc) from None
    except store.FeatureError as exc:
        raise _common.refused(exc) from None
    return {"task": task, "created": created}


def _slices_call(fn, feature_id: int):
    from lib.features import store
    from lib.tasks import feature_slices
    from lib.tasks.transcribe import TranscribeRefused
    _common.feature(feature_id)
    try:
        return fn(feature_id)
    except (store.FeatureError, feature_slices.SlicesRefused,
            feature_slices.SlicesRejected, TranscribeRefused) as exc:
        raise _common.refused(exc) from None


def feature_slices(feature_id: int) -> dict:
    """The spec's slice table as regin reads it: the refusal verbatim, or the
    rows with the task each became.

    Args:
        feature_id: The feature's id.
    """
    from lib.tasks import feature_slices as slices
    return _slices_call(slices.report, feature_id)


def feature_transcribe(feature_id: int) -> dict:
    """Land the spec's slice table as idea tasks and dependency edges.
    Arms nothing.

    Args:
        feature_id: The feature's id.
    """
    from lib.tasks import feature_slices as slices
    return _slices_call(lambda fid: slices.transcription_summary(
        slices.transcribe_feature(fid)), feature_id)


TOOLS = (feature_list, feature_get, feature_create, feature_set_spec,
         feature_update, feature_lifecycle, feature_link, feature_attribute,
         feature_open_step, feature_slices, feature_transcribe)
