"""Which feature a session was building.

A join table rather than a column on `sessions`, and the shape mirrors
`session_repos` deliberately: a column cannot hold two features, and sessions
that name two already exist. `is_primary` carries the same meaning it does
there — the one this session mostly *was* — and it is what stops a shared
session's cost being counted twice (`lib/features/cost.py`).

**An unattributed session is the absence of a row**, never a sentinel value.
Most sessions are in that state and most always will be: nothing infers an
attribution from a cwd, a branch name or a clock, so a row exists only because
a human or a task link asserted it.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Column, Field, Integer, String, Text, text

from lib.orm.base import Base

DECLARED = "declared"
TASK_LINK = "task-link"
RECALLED = "recalled"
#: `task-link` is derived at read time from `agent_task_links` rather than
#: written here — the link is the tracker's own live assertion, and copying it
#: into a row would make the ledger a stale second opinion. The value is part
#: of the column's domain because a ledger row carries one of the three.
PROVENANCES = (DECLARED, TASK_LINK, RECALLED)


class SessionFeature(Base, table=True):
    """One session's declaration that it was building one feature."""

    __tablename__ = "session_features"

    trace_id: str = Field(
        sa_column=Column("trace_id", String, primary_key=True))
    feature_key: str = Field(
        sa_column=Column("feature_key", String, primary_key=True))
    is_primary: int = Field(
        sa_column=Column("is_primary", Integer, nullable=False,
                         server_default=text("0")))
    provenance: str = Field(
        default=DECLARED,
        sa_column=Column("provenance", Text, nullable=False,
                         server_default=text("'declared'")))
    # The round the tracker reported at the instant the claim was made —
    # cached the way `agent_task_links.snapshot_json` caches its issue. NULL on
    # a `recalled` row, which has no such instant: writing today's round there
    # would look like a measurement nothing downstream could tell from a real
    # one.
    round: Optional[int] = Field(
        default=None, sa_column=Column("round", Integer))
    created_at: Optional[str] = Field(
        default=None,
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")))
