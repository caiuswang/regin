"""Sessions regin launched itself through the Claude Agent SDK.

The SDK tier's identity record — what `bridge_panes` is to the tmux tier. One
mutable row per `trace_id`, written by `lib/agent_sdk/`, and the fact steering
routes on: an SDK-owned session answers an `AskUserQuestion` over a typed
in-process channel, while a user-started session can only be reached by typing
into its pane.

`status` outlives the process deliberately. A runner killed with the server
leaves a `running` row that no longer has a live channel behind it, so readers
must treat the row as a claim about intent, not proof of liveness — `pid` is
what confirms it.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Column, Field, Integer, String, Text
from sqlalchemy import UniqueConstraint, text

from lib.orm.base import Base

RUN_STATUSES: tuple[str, ...] = ("starting", "running", "exited", "failed")


class AgentRun(Base, table=True):
    """One regin-launched agent session."""

    __tablename__ = "agent_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    trace_id: str = Field(
        sa_column=Column("trace_id", String, nullable=False, unique=True))
    status: str = Field(
        default="starting",
        sa_column=Column("status", String, nullable=False,
                         server_default=text("'starting'")))
    pid: Optional[int] = Field(default=None,
                               sa_column=Column("pid", Integer))
    cwd: Optional[str] = Field(default=None, sa_column=Column("cwd", Text))
    model: Optional[str] = Field(default=None, sa_column=Column("model", Text))
    detail: Optional[str] = Field(default=None,
                                  sa_column=Column("detail", Text))
    # The child `claude` session this run is also traced as. NULL until the
    # child reports its id, so readers treat NULL as "not aliased" rather than
    # as a missing write.
    cli_session_id: Optional[str] = Field(
        default=None, sa_column=Column("cli_session_id", Text, index=True))
    created_at: str = Field(
        sa_column=Column("created_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))
    updated_at: str = Field(
        sa_column=Column("updated_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))


class AgentRunSession(Base, table=True):
    """One `claude` session a run has been traced as — the run's alias group.

    `AgentRun.cli_session_id` holds a single child and cannot hold two, but a
    run accumulates them: `/clear` ends the child conversation and the CLI
    opens another under a fresh id while the same runner keeps driving it. The
    single column was written before that was modelled, so the second child was
    refused as a conflict and every durable reader — `find_run`, the dual-writer
    guard, the resume picker, the settle sweep — went on seeing only the first.

    Append-only, one row per `(run, child)` pair — neither side unique alone. A
    run has many children (that is what `/clear` makes) and a child appears
    under several runs, because resuming keeps the child's session id
    (`fork_session=False`) and leaves the previous `exited` row still naming
    it. `reap_orphaned_runs` depends on that shape: it subtracts every id a
    LIVE run names before settling a dead one's.

    Deliberately NOT what the trace view unions on. `AgentRun.cli_session_id`
    stays pinned to the FIRST child and keeps that job, because a `/clear`
    opens a new conversation rather than continuing the old one — folding the
    heir's spans into the predecessor's session would fuse two sessions a
    person deliberately separated.
    """

    __tablename__ = "agent_run_sessions"
    __table_args__ = (UniqueConstraint("trace_id", "cli_session_id",
                                       name="uq_agent_run_sessions_pair"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    trace_id: str = Field(
        sa_column=Column("trace_id", String, nullable=False, index=True))
    cli_session_id: str = Field(
        sa_column=Column("cli_session_id", String, nullable=False))
    created_at: str = Field(
        sa_column=Column("created_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))
