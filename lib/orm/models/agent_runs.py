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
from sqlalchemy import text

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
    created_at: str = Field(
        sa_column=Column("created_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))
    updated_at: str = Field(
        sa_column=Column("updated_at", String, nullable=False,
                         server_default=text("(datetime('now'))")))
