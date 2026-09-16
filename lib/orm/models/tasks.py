"""Captured task ideas (`agent_tasks`) — the intent layer in front of
`lib/agent_sdk`.

A task is an idea first. Capture is cheap and reachable from third parties,
so nothing here carries an execution parameter: there is no `cwd`, no
`model`, no `permission_mode`, and `status` is always `idea` at capture.
Arming and execution are a later, user-authenticated surface.

`status` is a string rather than a boolean because the lifecycle grows
(`armed`/`queued`/`running`/`done`/`failed`); an extensible category column
beats a flag that has to be replaced.

`UNIQUE(source, external_id)` is the idempotency guarantee for redelivered
third-party captures. NULLs do not collide in a unique index on SQLite or
MySQL, so hand-typed captures with no external id coexist freely.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Column, Field, Integer, LargeBinary, String, Text
from sqlalchemy import UniqueConstraint, text

from lib.orm.base import Base

TASK_STATUS_IDEA = "idea"
TASK_STATUS_ARMED = "armed"
TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_DONE = "done"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_ARCHIVED = "archived"
TASK_STATUSES: tuple[str, ...] = (
    TASK_STATUS_IDEA, TASK_STATUS_ARMED, TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING, TASK_STATUS_DONE, TASK_STATUS_FAILED,
    TASK_STATUS_ARCHIVED,
)

RUN_STATUS_STARTING = "starting"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_DONE = "done"
RUN_STATUS_FAILED = "failed"
# A run row that has not been settled by its completion callback yet. Not a
# liveness claim — see `AgentTaskRun`.
RUN_OPEN_STATUSES: tuple[str, ...] = (RUN_STATUS_STARTING, RUN_STATUS_RUNNING)

# Priority is an ordered enum stored as a string: the order lives in
# `PRIORITY_ORDER` (lower sorts first, Linear's convention) rather than in the
# column, so adding a level never renumbers the rows already written.
TASK_PRIORITY_NONE = "none"
TASK_PRIORITY_LOW = "low"
TASK_PRIORITY_MEDIUM = "medium"
TASK_PRIORITY_HIGH = "high"
TASK_PRIORITY_URGENT = "urgent"
TASK_PRIORITIES: tuple[str, ...] = (
    TASK_PRIORITY_URGENT, TASK_PRIORITY_HIGH, TASK_PRIORITY_MEDIUM,
    TASK_PRIORITY_LOW, TASK_PRIORITY_NONE,
)
PRIORITY_ORDER: dict[str, int] = {
    name: rank for rank, name in enumerate(TASK_PRIORITIES)
}

TITLE_MAX = 200
# The authenticated edit path's bound. Raised from 8,000 because a feature's
# spec now lives in this column: the largest real artifact measured is 29,488
# characters, so the bound has to clear 32,000 rather than sit at it. It
# refuses rather than truncating — a spec silently clipped mid-sentence is
# worse than one the operator is told did not fit.
BODY_MAX = 64_000
SOURCE_MAX = 40
EXTERNAL_ID_MAX = 200


class AgentTask(Base, table=True):
    """One captured idea."""

    __tablename__ = "agent_tasks"
    __table_args__ = (UniqueConstraint("source", "external_id",
                                       name="uq_agent_tasks_source_external"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(sa_column=Column("title", Text, nullable=False))
    body: Optional[str] = Field(default=None, sa_column=Column("body", Text))
    status: str = Field(
        default=TASK_STATUS_IDEA,
        sa_column=Column("status", String, nullable=False,
                         server_default=text("'idea'")))
    source: str = Field(sa_column=Column("source", String, nullable=False))
    external_id: Optional[str] = Field(
        default=None, sa_column=Column("external_id", String))
    tags_json: Optional[str] = Field(
        default=None, sa_column=Column("tags_json", Text))
    links_json: Optional[str] = Field(
        default=None, sa_column=Column("links_json", Text))
    # NULL for machine-originated captures. A provenance label, not an
    # enforced join — see `lib/orm/models/api_keys.py`.
    created_by: Optional[int] = Field(
        default=None, sa_column=Column("created_by", Integer))
    created_at: str = Field(
        sa_column=Column("created_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))
    updated_at: str = Field(
        sa_column=Column("updated_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))
    # Execution parameters, written by the arm route alone. NULL is an inert
    # task: captured, never armed.
    params_json: Optional[str] = Field(
        default=None, sa_column=Column("params_json", Text))
    verify_cmd: Optional[str] = Field(
        default=None, sa_column=Column("verify_cmd", Text))
    # A per-task grant, not a global mode: 1 means a task whose dependencies
    # just resolved launches itself instead of waiting for a human.
    autorun: int = Field(
        default=0,
        sa_column=Column("autorun", Integer, nullable=False,
                         server_default=text("0")))
    # A label, not a parent: NULL is backlog. No FK, for the same reason
    # `created_by` carries none — a deleted project must not cascade away the
    # tasks that outlive it; the serializer resolves an unknown id to null.
    project_id: Optional[int] = Field(
        default=None, sa_column=Column("project_id", Integer, index=True))
    priority: str = Field(
        default=TASK_PRIORITY_NONE,
        sa_column=Column("priority", String, nullable=False,
                         server_default=text("'none'")))
    # The sort key for `priority`, denormalized because the alternative does
    # not work: ordering by a CASE over the words is unindexable, and its
    # value cannot be read back off a serialized row, so the keyset cursor
    # cannot be built and page two 500s. Written by `lib/tasks/store.py`
    # wherever `priority` is.
    priority_rank: int = Field(
        default=PRIORITY_ORDER[TASK_PRIORITY_NONE],
        sa_column=Column("priority_rank", Integer, nullable=False,
                         server_default=text(
                             str(len(TASK_PRIORITIES) - 1))))
    # The feature grain: NULL is a top-level task, which is what a feature is.
    # Depth is exactly two — a task may take a parent only if that parent has
    # none — so a cycle needs a child that is also a parent and cannot exist.
    # No FK, for the reason `project_id` carries none.
    parent_id: Optional[int] = Field(
        default=None, sa_column=Column("parent_id", Integer, index=True))
    # `users.id`. Edit-only: claiming is an act an authenticated human performs
    # about themselves, and capture is reachable by a machine key.
    assignee: Optional[int] = Field(
        default=None, sa_column=Column("assignee", Integer))
    # `rounds` is `1 + this` and is never itself stored, so the count and the
    # reported number cannot drift. One edge increments it: a child run
    # starting over a parent whose `done` was an operator's ship claim.
    completions_walked_back: int = Field(
        default=0,
        sa_column=Column("completions_walked_back", Integer, nullable=False,
                         server_default=text("0")))
    # The done-claim, recorded rather than read off `status`. `status` is the
    # task lifecycle and `complete_run` writes it when a run settles; a column
    # carrying both axes let a run settling ship a feature nobody claimed.
    # NULL means never claimed, which is a different fact from `status` being
    # anything in particular.
    claimed_at: Optional[str] = Field(
        default=None, sa_column=Column("claimed_at", String))


class AgentTaskRun(Base, table=True):
    """One attempt at a task — append-only, never updated in place except to
    settle it.

    `trace_id` joins `agent_runs.trace_id`, which brings cost, spans and grade
    with it rather than duplicating any of them here. It is NULL for the window
    between writing this row and the launch that mints the id: a crash there
    leaves a visible `starting` row instead of an untracked child.

    `status` is a claim about intent exactly as `AgentRun.status` is — it
    outlives the process that wrote it, so a reader merges it with the liveness
    of the run behind `trace_id` rather than trusting `running`.

    `UNIQUE(task_id, attempt)` is what makes a concurrent double-start
    impossible: two requests that both pass the liveness check compute the same
    next attempt, and the constraint picks the winner — the same way
    `UNIQUE(source, external_id)` resolves a capture replay, rather than a lock
    or a window.
    """

    __tablename__ = "agent_task_runs"
    __table_args__ = (UniqueConstraint("task_id", "attempt",
                                       name="uq_agent_task_runs_attempt"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(
        sa_column=Column("task_id", Integer, nullable=False, index=True))
    trace_id: Optional[str] = Field(
        default=None, sa_column=Column("trace_id", String, index=True))
    attempt: int = Field(
        default=1,
        sa_column=Column("attempt", Integer, nullable=False,
                         server_default=text("1")))
    status: str = Field(
        default=RUN_STATUS_STARTING,
        sa_column=Column("status", String, nullable=False,
                         server_default=text("'starting'")))
    started_at: str = Field(
        sa_column=Column("started_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))
    ended_at: Optional[str] = Field(
        default=None, sa_column=Column("ended_at", String))
    exit_reason: Optional[str] = Field(
        default=None, sa_column=Column("exit_reason", Text))
    verify_exit_code: Optional[int] = Field(
        default=None, sa_column=Column("verify_exit_code", Integer))


class AgentTaskDep(Base, table=True):
    """One edge of the task DAG: `task_id` waits for `depends_on_id`.

    An edge table rather than an `after: []` column because both directions are
    queried — what blocks a task, and what a finished task unblocks. Acyclicity
    is enforced by the writer (`lib/tasks/deps.py`) before the insert, so no
    reader has to defend against a cycle.
    """

    __tablename__ = "agent_task_deps"

    task_id: int = Field(
        sa_column=Column("task_id", Integer, primary_key=True, nullable=False))
    depends_on_id: int = Field(
        sa_column=Column("depends_on_id", Integer, primary_key=True,
                         nullable=False))


class AgentTaskImage(Base, table=True):
    """An image attached to a captured idea.

    Separate from `BridgeAttachment` because the two are keyed to different
    things: an attachment belongs to a *trace*, and a task has no trace until
    someone runs it — which may be never, or three times. The bytes therefore
    hang off the task and are re-read on every attempt, rather than being
    claimed and spent by one prompt.

    `idx` is a per-task display and delivery order, allocated by the writer.
    No `[Image #N]` marker travels in the text: a task's prompt is its title
    and body, and the images ride the launch as content blocks, so nothing in
    the sentence has to name them.
    """

    __tablename__ = "agent_task_images"
    __table_args__ = (UniqueConstraint("task_id", "idx",
                                       name="uq_agent_task_images_idx"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(
        sa_column=Column("task_id", Integer, nullable=False, index=True))
    idx: int = Field(sa_column=Column("idx", Integer, nullable=False))
    media_type: str = Field(
        sa_column=Column("media_type", String, nullable=False))
    bytes_: bytes = Field(
        sa_column=Column("bytes", LargeBinary, nullable=False))
    byte_size: int = Field(
        sa_column=Column("byte_size", Integer, nullable=False))
    sha256: str = Field(sa_column=Column("sha256", String, nullable=False))
    created_at: str = Field(
        sa_column=Column("created_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))


class AgentTaskLink(Base, table=True):
    """A task's link to an issue in ANOTHER tracker.

    Only another tracker: a native feature needs no row here, because a regin
    task IS the issue, and a link from one to another would be the migration
    `feature_key`-as-identity forbids.

    A row, not a `links_json` entry, because this link is *queried* — by
    provider, to answer "what is configured", and by key, to resolve a
    redelivery — while `links_json` is a free-text bookmark list the task
    carries for display only.

    The issue itself lives in `IssueCache`, keyed by the feature rather than
    by this row: a feature key may have no link and may have two, so a cache
    hanging off the link was reachable only through whichever task happened to
    hold it.

    `sync_error` stays here, because it is a property of one operator's
    assertion rather than of the issue — a link whose last fetch failed still
    renders the cached issue plus the reason.

    `UNIQUE(task_id, provider, external_key)` makes re-attaching the same
    issue idempotent, the same way `UNIQUE(source, external_id)` resolves a
    capture replay.
    """

    __tablename__ = "agent_task_links"
    __table_args__ = (
        UniqueConstraint("task_id", "provider", "external_key",
                         name="uq_agent_task_links_ref"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(
        sa_column=Column("task_id", Integer, nullable=False, index=True))
    provider: str = Field(
        sa_column=Column("provider", String, nullable=False))
    external_key: str = Field(
        sa_column=Column("external_key", String, nullable=False))
    url: str = Field(sa_column=Column("url", Text, nullable=False))
    sync_error: Optional[str] = Field(
        default=None, sa_column=Column("sync_error", Text))
    created_at: str = Field(
        sa_column=Column("created_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))


class IssueCache(Base, table=True):
    """The normalized `ExternalIssue` regin last fetched for a feature key.

    Keyed `(provider, external_key)` — the feature — and not by a link row, so
    the task-link card and the feature ledger read one fact and cannot report
    different stages for the same issue.

    Every successful fetch writes it, from CLI or web, so a CLI run warms the
    web. Only the web reads it: the cache feeds display, never a decision, and
    a stage the CLI reported from a stale snapshot would change the escape
    counts in its own ledger.

    A failed fetch writes nothing. Freshness is therefore two states — never
    fetched, or fetched at T — and there is no row that means "tried and
    failed", which would be a third that no reader could act on.
    """

    __tablename__ = "issue_cache"

    provider: str = Field(
        sa_column=Column("provider", String, primary_key=True))
    external_key: str = Field(
        sa_column=Column("external_key", String, primary_key=True))
    snapshot_json: str = Field(
        sa_column=Column("snapshot_json", Text, nullable=False))
    synced_at: str = Field(
        sa_column=Column("synced_at", String, nullable=False))
