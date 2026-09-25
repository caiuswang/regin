"""The writer and reader of `features` — the population, and nothing else.

**The population is this table.** `SELECT * FROM features`, with no filter and
no union. That single sentence is the redesign: before it, membership was a
five-arm union of strings across `session_features`, `defects`,
`agent_task_links` and `agent_tasks`, so four surfaces answered "what counts as
a feature" four different ways — two wider than the authoritative reading, one
narrower — and none of the five arms was the done-claim, which is how a feature
could be shipped and still not appear in the list.

Creating a feature is **one transaction over two tables**: the row, and its
first `specced` event. That is what makes "every feature has a stage" a
structural fact rather than a convention a caller could forget — there is no
sixth answer meaning "not synced yet".

A feature is never deleted, only abandoned. Deleting one would take its events
with it, and the events are the only source of `rounds` and the escape verdict,
so a delete is not a cleanup — it is a rewrite of history.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.features import events as events_module
from lib.orm import SessionLocal
from lib.orm.models.features import (
    ORIGIN_NATIVE, OPERATOR, TITLE_MAX, Feature, FeatureEvent,
)
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import AgentTask
from lib.tasks.integrations.base import DESCRIPTION_MAX

log = get_activity_logger("defects")


class FeatureError(ValueError):
    """A write that would leave the ledger untrustworthy."""


class ProjectRejected(FeatureError):
    """A `project_id` no registered repo answers to, or of the wrong type.

    Distinct from a bare `FeatureError` so the 400 can name the offending
    field: a client that highlights `field` would otherwise point at the title
    while complaining about the project.
    """

    field = "project_id"


class NotRunnable(FeatureError):
    """The feature holds no task, so there is no attempt to make.

    A distinct refusal from "this feature is finished": a feature with zero
    tasks is a perfectly valid record — it can be specced, shipped and blamed —
    it simply has nothing for an agent to run.
    """


def create(title: str, *, body: Optional[str] = None,
           origin: str = ORIGIN_NATIVE, external_ref: Optional[str] = None,
           external_url: Optional[str] = None,
           sync_error: Optional[str] = None,
           declared_by: str = OPERATOR,
           trace_id: Optional[str] = None,
           project_id: Optional[int] = None) -> Feature:
    """A feature row and its `specced` event, in one transaction."""
    name = (title or "").strip()
    if not name:
        raise FeatureError("a feature needs a title")
    if len(name) > TITLE_MAX:
        raise FeatureError(f"a title is at most {TITLE_MAX} characters")
    if body and len(body) > DESCRIPTION_MAX:
        raise FeatureError(
            f"a spec is at most {DESCRIPTION_MAX} characters; this one is "
            f"{len(body)}")
    check_project(project_id)
    with SessionLocal() as session:
        row = Feature(title=name, body=body, origin=origin,
                      external_ref=external_ref, external_url=external_url,
                      sync_error=sync_error, project_id=project_id)
        session.add(row)
        session.flush()
        session.add(FeatureEvent(feature_id=row.id,
                                 kind=events_module.SPECCED,
                                 declared_by=declared_by, trace_id=trace_id))
        session.commit()
        session.refresh(row)
    log.write("feature_created", feature_id=row.id, origin=origin,
              external_ref=external_ref)
    return row


def get(feature_id: int) -> Optional[Feature]:
    with SessionLocal() as session:
        return session.get(Feature, feature_id)


def require(feature_id: int) -> Feature:
    row = get(feature_id)
    if row is None:
        raise FeatureError(f"no feature {feature_id}")
    return row


def population() -> list[Feature]:
    """Every feature regin knows. Unfiltered, by definition."""
    with SessionLocal() as session:
        rows = list(session.exec(select(Feature).order_by(Feature.id)).all())
    log.read("feature_population_read", count=len(rows))
    return rows


def by_external(origin: str, external_ref: str) -> Optional[Feature]:
    """The feature a remote reference names, or None."""
    with SessionLocal() as session:
        return session.exec(
            select(Feature).where(Feature.origin == origin,
                                  Feature.external_ref == external_ref)).first()


def rename(feature_id: int, title: str) -> Feature:
    """Edit the feature's own title.

    Independent of its tasks' titles from the moment it is created: a
    feature's title answers *what is being delivered* and a task's answers
    *what this step does*, and the two legitimately diverge. No surface falls
    back to a task's title, so this column is the only writer of the name.
    """
    name = (title or "").strip()
    if not name:
        raise FeatureError("a feature needs a title")
    with SessionLocal() as session:
        row = session.get(Feature, feature_id)
        if row is None:
            raise FeatureError(f"no feature {feature_id}")
        row.title = name[:TITLE_MAX]
        session.add(row)
        session.commit()
        session.refresh(row)
    log.write("feature_renamed", feature_id=feature_id)
    return row


def set_body(feature_id: int, body: Optional[str]) -> Feature:
    """Replace the spec."""
    if body and len(body) > DESCRIPTION_MAX:
        raise FeatureError(
            f"a spec is at most {DESCRIPTION_MAX} characters; this one is "
            f"{len(body)}")
    with SessionLocal() as session:
        row = session.get(Feature, feature_id)
        if row is None:
            raise FeatureError(f"no feature {feature_id}")
        row.body = body
        session.add(row)
        session.commit()
        session.refresh(row)
    log.write("feature_spec_edited", feature_id=feature_id)
    return row


def check_project(project_id) -> None:
    """Refuse a `project_id` that is neither `None` nor a registered repo id."""
    if project_id is None:
        return
    if not isinstance(project_id, int) or isinstance(project_id, bool):
        raise ProjectRejected("project_id must be an integer or null")
    with SessionLocal() as session:
        if session.get(Repo, project_id) is None:
            raise ProjectRejected(f"unknown project: {project_id}")


def set_project(feature_id: int, project_id: Optional[int]) -> Feature:
    """File the feature under a registered repo, or unfile it with `None`.

    Checked against `repos` rather than trusted, for the reason
    `lib.tasks.store._resolved_project` states: there is no foreign key, so a
    feature pointing at a repo dropped between two requests would render as an
    unresolvable chip forever.

    Appends no event. A project is where a feature lives, not a step in its
    life, and putting it in the append-only log would make `rounds` count it.
    """
    check_project(project_id)
    with SessionLocal() as session:
        row = session.get(Feature, feature_id)
        if row is None:
            raise FeatureError(f"no feature {feature_id}")
        row.project_id = project_id
        session.add(row)
        session.commit()
        session.refresh(row)
    log.write("feature_project_set", feature_id=feature_id,
              project_id=project_id)
    return row


def project_name(project_id: Optional[int]) -> Optional[str]:
    """The repo's display name, or None when unfiled or since dropped."""
    if project_id is None:
        return None
    with SessionLocal() as session:
        repo = session.get(Repo, project_id)
    return repo.name if repo else None


# ── the operator's two declarations ───────────────────────────

def ship(feature_id: int, *, declared_by: str = OPERATOR,
         trace_id: Optional[str] = None):
    """The done-claim. This is the boundary later defects are blamed against."""
    require(feature_id)
    return events_module.append(feature_id, events_module.SHIPPED,
                                declared_by=declared_by, trace_id=trace_id)


def abandon(feature_id: int, *, declared_by: str = OPERATOR,
            trace_id: Optional[str] = None):
    """Take the feature down. Costs no round — nothing was walked back."""
    require(feature_id)
    return events_module.append(feature_id, events_module.ABANDONED,
                                declared_by=declared_by, trace_id=trace_id)


# ── tasks ─────────────────────────────────────────────────────

def adopt_task(feature_id: int, task_id: int) -> None:
    """Place a task under a feature.

    Writes no event: which feature a task belongs to is attribution, not
    lifecycle, and a task changing hands says nothing about whether the work
    has started.

    Re-homing is an UPDATE of one column, which is the whole guarantee — there
    is no second row for "two features" to be written into.
    """
    require(feature_id)
    with SessionLocal() as session:
        row = session.get(AgentTask, task_id)
        if row is None:
            raise FeatureError(f"no task {task_id}")
        row.feature_id = feature_id
        session.add(row)
        session.commit()
    log.write("feature_task_adopted", feature_id=feature_id, task_id=task_id)


def release_task(task_id: int) -> None:
    """Detach a task from whatever feature holds it."""
    with SessionLocal() as session:
        row = session.get(AgentTask, task_id)
        if row is None:
            raise FeatureError(f"no task {task_id}")
        row.feature_id = None
        session.add(row)
        session.commit()
    log.write("feature_task_released", task_id=task_id)


def tasks_of(feature_id: int) -> list[AgentTask]:
    with SessionLocal() as session:
        return list(session.exec(
            select(AgentTask).where(AgentTask.feature_id == feature_id)
            .order_by(AgentTask.id)).all())


def feature_of_task(task_id: int) -> Optional[Feature]:
    with SessionLocal() as session:
        task = session.get(AgentTask, task_id)
        if task is None or task.feature_id is None:
            return None
        return session.get(Feature, task.feature_id)


def start_run(feature_id: int, task_id: Optional[int] = None) -> dict:
    """Run one of this feature's tasks. Refuses a feature that holds none.

    `task_id` names which, and is required once a feature holds more than one:
    picking for the operator would be regin deciding what the next step is,
    which is exactly the inference the pipeline forbids.
    """
    from lib.tasks import runs  # noqa: PLC0415  (runs imports this module)

    tasks = tasks_of(feature_id)
    if not tasks:
        raise NotRunnable(
            f"feature {feature_id} holds no task, so there is nothing to run "
            "— it can still be shipped, blamed and reported on")
    if task_id is None:
        if len(tasks) > 1:
            raise NotRunnable(
                f"feature {feature_id} holds {len(tasks)} tasks — name the "
                "one to run")
        task_id = tasks[0].id
    elif all(task.id != task_id for task in tasks):
        raise NotRunnable(f"task {task_id} is not a task of {feature_id}")
    return runs.start_run(task_id)
