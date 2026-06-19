"""Post-hoc rubric grades for captured sessions (`lib/grader/`).

One row per (session, axis, grading run). The two axes — `correctness`
(claim groundedness / coverage / source quality) and `process` (tool-use
appropriateness, redundancy, reliability, cost-proportionality) — are
graded separately and deliberately never fused into one number; any
cross-axis aggregate (the cost-per-correct-outcome Pareto framing) is
computed at the analytics layer from the stored pairs.

The table is append-only by convention: re-grading a session inserts a
new row and readers take the latest row per (trace_id, axis), so a
grade's provenance (tier, judge, rubric version) is never overwritten.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Column, Field, Integer, String, Text
from sqlalchemy import Index, text

from lib.orm.base import Base


class SessionGrade(Base, table=True):
    """One axis-grade produced by a single grading run.

    The verdict/axis/tier vocabularies live with the grading code
    (`lib/grader/service.py`, `correctness.py`, `process.py`) — this model
    only persists them.
    """

    __tablename__ = "session_grades"
    # Declared to match db/schema.sql so Alembic autogenerate doesn't
    # propose dropping the real indexes. The created_at index is DESC in
    # the DDL; metadata declares the plain column because a textual
    # "created_at DESC" element never compares equal to the reflected
    # index and would round-trip on every autogenerate.
    __table_args__ = (
        Index("idx_session_grades_trace", "trace_id", "axis"),
        Index("idx_session_grades_created", "created_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    # Session under grade (Claude Code session id == trace_id).
    trace_id: str = Field(
        sa_column=Column("trace_id", String, nullable=False))
    axis: str = Field(sa_column=Column("axis", String, nullable=False))
    verdict: str = Field(sa_column=Column("verdict", String, nullable=False))
    # 'screen' = mechanical-only pass; 'deep' = LLM-assisted pass.
    tier: str = Field(
        sa_column=Column("tier", String, nullable=False,
                         server_default=text("'screen'")))
    # JSON: per-criterion counters/ratios (the one-line scoreboard, as data).
    scoreboard: str = Field(
        sa_column=Column("scoreboard", Text, nullable=False,
                         server_default=text("'{}'")))
    # Mandated human-readable output: scoreboard line + one bullet per failure.
    report: str = Field(
        sa_column=Column("report", Text, nullable=False,
                         server_default=text("''")))
    # JSON: full claim ledger with per-claim verdicts (correctness) or
    # per-criterion episode evidence (process). The audit trail.
    detail: str = Field(
        sa_column=Column("detail", Text, nullable=False,
                         server_default=text("'{}'")))
    rubric_version: Optional[str] = Field(
        default=None, sa_column=Column("rubric_version", String))
    # 'mechanical' or the external judge agent id that assisted.
    judge: Optional[str] = Field(default=None, sa_column=Column("judge", String))
    is_test: int = Field(
        sa_column=Column("is_test", Integer, nullable=False,
                         server_default=text("0")))
    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False,
                         server_default=text("(datetime('now'))")))


__all__ = ["SessionGrade"]
