"""Partial index over the PENDING decision spans the blocker feed derives from.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-01

`lib/agent_messages/blockers._parked_trace_ids` asks "which sessions hold an
unresolved decision span" on every poll of the blocker banner. Answering it
without an index is a full scan of ``session_spans`` — 524k rows and ~372ms on
the author's DB, growing linearly with trace history forever, on a query whose
answer is a handful of rows.

The predicate mirrors ``blockers._decision_predicate()`` exactly, and must keep
mirroring it: SQLite uses a partial index only when the query's WHERE terms are
syntactically implied by the index's, which is also why that helper spells the
span names as literals instead of bound parameters. Narrowing it to the decision
spans (2.4k rows) rather than every PENDING span (75k) is the difference between
a 1ms probe and a 58ms one; the unindexed scan it replaces was 372ms.

The canonical final shape lives in ``db/schema.sql``; fresh installs run that
directly.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_session_spans_pending "
    "ON session_spans(trace_id) "
    "WHERE status_code = 'PENDING' "
    "AND (name IN ('permission.request','tool.AskUserQuestion',"
    "'tool.ExitPlanMode') OR span_id LIKE 'permreq-%')")


def upgrade() -> None:
    op.execute(_INDEX)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_session_spans_pending")
