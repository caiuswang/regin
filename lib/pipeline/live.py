"""`pipeline` frames: a signal that a task, feature or defect row just changed.

One seam for every writer. The rows these frames describe are written from
dozens of sites across `lib/tasks`, `lib/features` and `lib/defects`, and a
broadcast call placed beside each one is a list that goes stale the first time
someone adds a writer. So the frames are raised from the ORM session instead:
`after_flush` records which pipeline rows the flush touched, `after_commit`
announces them, and a rollback discards them — a write that did not land is
never announced.

A frame is a refresh signal, not a copy of the row: it names the entity and
its id, and each open view re-reads its own data through the API.

Transport depends on the process. Inside the server (the web routes and the
task runs it hosts) the frames go straight to `hub.broadcast_event`. A CLI
process has no reach into the server's streams, so it hands them to the
server over the loopback notify route (`use_loopback`), which re-raises them.
"""

from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from lib.activity_log import get_activity_logger

log = get_activity_logger("notifications")

PIPELINE_EVENT = "pipeline"
PIPELINE_TOPIC = "pipeline"
ENTITIES = frozenset({"task", "feature", "defect"})

_PENDING_KEY = "pipeline_live_pending"

# (entity, id) -> project_id, or `_LOOKUP` when only the owning feature knows it.
_LOOKUP = object()


def _task_row(obj):
    return ("task", obj.id, obj.project_id)


def _task_child(obj):
    return ("task", obj.task_id, _LOOKUP)


def _feature_row(obj):
    return ("feature", obj.id, obj.project_id)


def _feature_event(obj):
    return ("feature", obj.feature_id, _LOOKUP)


def _defect_row(obj):
    return ("defect", obj.id, _LOOKUP)


def _classifiers() -> dict[type, Callable]:
    from lib.orm.models.defects import Defect
    from lib.orm.models.features import Feature, FeatureEvent
    from lib.orm.models.tasks import AgentTask, AgentTaskDep, AgentTaskRun

    return {
        AgentTask: _task_row,
        AgentTaskRun: _task_child,
        AgentTaskDep: _task_child,
        Feature: _feature_row,
        FeatureEvent: _feature_event,
        Defect: _defect_row,
    }


_by_type: Optional[dict[type, Callable]] = None


def _classify(obj) -> Optional[tuple]:
    global _by_type
    if _by_type is None:
        _by_type = _classifiers()
    fn = _by_type.get(type(obj))
    return fn(obj) if fn is not None else None


def mark(session: Session, entity: str, entity_id: int) -> None:
    """Announce a row the flush cannot see — one removed by a bulk
    `delete()` statement, which bypasses the identity map."""
    session.info.setdefault(_PENDING_KEY, {})[(entity, int(entity_id))] = _LOOKUP


def _after_flush(session: Session, _ctx) -> None:
    touched = [*session.new, *session.deleted,
               *(o for o in session.dirty if session.is_modified(o))]
    pending = None
    for obj in touched:
        hit = _classify(obj)
        if hit is None or hit[1] is None:
            continue
        if pending is None:
            pending = session.info.setdefault(_PENDING_KEY, {})
        entity, entity_id, project = hit
        if pending.get((entity, entity_id), _LOOKUP) is _LOOKUP:
            pending[(entity, entity_id)] = project


def _after_commit(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    try:
        _deliver(_frames(pending))
    except Exception:  # noqa: BLE001 — a refresh signal must never fail the write
        log.error("pipeline_event_failed", exc_info=True)


def _after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


def _frames(pending: dict) -> list[dict]:
    unresolved = {key for key, project in pending.items() if project is _LOOKUP}
    projects = _lookup_projects(unresolved) if unresolved else {}
    frames = []
    for (entity, entity_id), project in sorted(pending.items()):
        if project is _LOOKUP:
            project = projects.get((entity, entity_id))
        frames.append({"entity": entity, "id": entity_id, "project_id": project})
    return frames


def _lookup_projects(keys: set) -> dict:
    """Project ids for rows that do not carry one, read after the commit.

    A fresh session: the committing one cannot emit SQL from `after_commit`.
    A row deleted by the commit is not found and reads as no project.
    """
    from lib.orm import SessionLocal
    from lib.orm.models.defects import Defect
    from lib.orm.models.features import Feature
    from lib.orm.models.tasks import AgentTask

    ids = {e: {i for (k, i) in keys if k == e} for e in ENTITIES}
    out: dict = {}
    with SessionLocal() as s:
        if ids["task"]:
            for tid, pid in s.execute(select(AgentTask.id, AgentTask.project_id)
                                      .where(AgentTask.id.in_(ids["task"]))):
                out[("task", tid)] = pid
        if ids["feature"]:
            for fid, pid in s.execute(select(Feature.id, Feature.project_id)
                                      .where(Feature.id.in_(ids["feature"]))):
                out[("feature", fid)] = pid
        if ids["defect"]:
            for did, pid in s.execute(
                    select(Defect.id, Feature.project_id)
                    .outerjoin(Feature, Feature.id == Defect.feature_id)
                    .where(Defect.id.in_(ids["defect"]))):
                out[("defect", did)] = pid
    return out


def broadcast(frames: list[dict]) -> None:
    from lib.notifications import hub

    for frame in frames:
        hub.broadcast_event(PIPELINE_EVENT, frame, topic=PIPELINE_TOPIC)


def _loopback(frames: list[dict]) -> None:
    from lib.notifications import notify

    notify.notify_pipeline(frames)


_loopback_mode = False


def _deliver(frames: list[dict]) -> None:
    if _loopback_mode:
        _loopback(frames)
    else:
        broadcast(frames)


def use_loopback() -> None:
    """This process is not the server: route frames over the loopback."""
    global _loopback_mode
    _loopback_mode = True


def use_in_process() -> None:
    global _loopback_mode
    _loopback_mode = False


def valid_frames(raw) -> list[dict]:
    """The frames a loopback body may carry, rebuilt field by field."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:500]:
        if not isinstance(item, dict) or item.get("entity") not in ENTITIES:
            continue
        try:
            entity_id = int(item["id"])
            project = item.get("project_id")
            project = None if project is None else int(project)
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"entity": item["entity"], "id": entity_id,
                    "project_id": project})
    return out


def register() -> None:
    for name, fn in (("after_flush", _after_flush),
                     ("after_commit", _after_commit),
                     ("after_rollback", _after_rollback)):
        if not event.contains(Session, name, fn):
            event.listen(Session, name, fn)


register()


__all__ = ["PIPELINE_EVENT", "PIPELINE_TOPIC", "broadcast", "mark",
           "use_loopback", "use_in_process", "valid_frames"]
