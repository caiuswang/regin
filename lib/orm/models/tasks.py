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

from sqlmodel import (
    Column, Field, ForeignKey, Integer, LargeBinary, String, Text,
)
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

TASK_KIND_SPEC = "spec"
TASK_KIND_BUILD = "build"
TASK_KIND_FIX = "fix"
TASK_KIND_TASK = "task"
TASK_KINDS: tuple[str, ...] = (
    TASK_KIND_SPEC, TASK_KIND_BUILD, TASK_KIND_FIX, TASK_KIND_TASK,
)

# An attempt that has been parameterised but not launched. The `armed` state
# used to live on the task as a status word plus three hidden columns; here it
# is the pending attempt itself, so "what will Run spend" is a row you can read.
RUN_STATUS_ARMED = "armed"
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

# What a dependency edge means. `requires` is satisfied only by a dependency
# that finished successfully; `after` is satisfied by one that finished at all,
# because the edge is an ordering the operator drew over unrelated work.
DEP_KIND_REQUIRES = "requires"
DEP_KIND_AFTER = "after"
DEP_KINDS: tuple[str, ...] = (DEP_KIND_REQUIRES, DEP_KIND_AFTER)

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
    #: Highest image `idx` ever issued to this row. Survives a detach, so a
    #: freed index is never handed to different bytes.
    image_high_water: int = Field(
        default=0,
        sa_column=Column("image_high_water", Integer, nullable=False,
                         server_default=text("0")))
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
    # The feature this task is a step of. ONE nullable column, which is what
    # makes "a task holding two feature keys" unwritable — the shape three
    # refusals in `lib/tasks/links.py` and an `ambiguous` CTE in every reader
    # used to defend against. NULL is a task nobody has placed under a
    # feature, which is a real state and not a gap.
    feature_id: Optional[int] = Field(
        default=None,
        sa_column=Column("feature_id", Integer,
                         ForeignKey("features.id", ondelete="SET NULL"),
                         index=True))
    # `users.id`. Edit-only: claiming is an act an authenticated human performs
    # about themselves, and capture is reachable by a machine key.
    assignee: Optional[int] = Field(
        default=None, sa_column=Column("assignee", Integer))
    # What the step is, orthogonal to `status` (where its runs are). Only ever
    # declared, never inferred from a title — legacy rows stay `task`.
    kind: str = Field(
        default=TASK_KIND_TASK,
        sa_column=Column("kind", String, nullable=False,
                         server_default=text("'task'")))
    # The title regin last generated. `title == auto_title` is what keeps a
    # title regin's to recompute; a hand edit breaks the equality and freezes it.
    auto_title: Optional[str] = Field(
        default=None, sa_column=Column("auto_title", Text))
    # The project stage this step was opened from (`project_stages.key`).
    # Orthogonal to `kind`, which carries the stage's role: the ledger reads
    # `kind`, so a custom stage never needs a new kind.
    stage_key: Optional[str] = Field(
        default=None, sa_column=Column("stage_key", Text))


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
    # The parameters this attempt actually spent. They sat on the task, so
    # trying the same work with different ones meant creating a second task —
    # which is how "a sub-task is a run" became a shape the model carried.
    # Here a rerun is a row, and the two attempts' parameters stay separate.
    params_json: Optional[str] = Field(
        default=None, sa_column=Column("params_json", Text))
    # A per-task grant, not a global mode: 1 means a task whose dependencies
    # just resolved launches itself instead of waiting for a human.
    autorun: int = Field(
        default=0,
        sa_column=Column("autorun", Integer, nullable=False,
                         server_default=text("0")))
    # Which of the two settle paths ended this attempt. The SDK callback knows
    # only that the session ended — an inference, and a wrong one for a session
    # aborted and resumed by hand — while the agent's own `task finish` is the
    # fact the dependency chain waits on. `ended_at` alone cannot tell them
    # apart, so the inference won by arriving first and the later report was
    # dropped. NULL is "not settled by the agent", which is what an unsettled
    # row and a pre-column row both are.
    settled_by: Optional[str] = Field(
        default=None, sa_column=Column("settled_by", Text))


class AgentTaskDep(Base, table=True):
    """One edge of the task DAG: `task_id` waits for `depends_on_id`.

    An edge table rather than an `after: []` column because both directions are
    queried — what blocks a task, and what a finished task unblocks. Acyclicity
    is enforced by the writer (`lib/tasks/deps.py`) before the insert, so no
    reader has to defend against a cycle.

    `kind` is what the edge MEANS, and it is a column because the graph was
    being asked to carry two intents in one shape. `requires` waits for its
    dependency to succeed; `after` waits only for it to have settled, because
    the edge was drawn to serialize unrelated work and a failure there says
    nothing about the dependent. A row written before this column existed is
    `requires` — the rule it was resolved under when it was drawn.
    """

    __tablename__ = "agent_task_deps"

    task_id: int = Field(
        sa_column=Column("task_id", Integer, primary_key=True, nullable=False))
    depends_on_id: int = Field(
        sa_column=Column("depends_on_id", Integer, primary_key=True,
                         nullable=False))
    kind: str = Field(
        default=DEP_KIND_REQUIRES,
        sa_column=Column("kind", String, nullable=False,
                         server_default=text("'requires'")))


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
