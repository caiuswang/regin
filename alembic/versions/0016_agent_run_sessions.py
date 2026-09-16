"""agent_run_sessions — every child session a run has been traced as.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-04

``0012`` gave a run one ``cli_session_id``, on the assumption that a run has one
child. ``/clear`` breaks that: the CLI ends the child conversation and opens
another under a fresh id while the same runner keeps driving it. The single
column could not hold the second, and ``store.set_cli_session`` refused it as a
conflict — so ``find_run``, the dual-writer guard, the resume picker and the
settle sweep all went on seeing only the first child, and an heir looked like a
session regin had never launched.

This table is the run's whole alias group: one row per ``(run, child)`` pair.
Neither side is unique on its own. A run has many children — that is what
``/clear`` makes — and a child appears under several runs, because resuming
keeps the child's session id (``fork_session=False``) and leaves the previous
``exited`` row still naming it. ``reap_orphaned_runs`` already relies on exactly
that shape: it subtracts every id a LIVE run names before settling a dead one's.

``agent_runs.cli_session_id`` is deliberately left alone, still pinned to the
FIRST child. It is what the trace view unions the run's SDK-stream spans with,
and a ``/clear`` opens a new conversation rather than continuing the old one:
re-pointing it would fuse two sessions a person deliberately separated, and
orphan the spans of the half it moved off.

The backfill copies today's links in, so every membership answer on an existing
install is identical the moment this runs.

The canonical final shape lives in ``db/schema.sql``; fresh installs run that
directly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_run_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("cli_session_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.UniqueConstraint("trace_id", "cli_session_id",
                            name="uq_agent_run_sessions_pair"),
    )
    op.create_index("idx_agent_run_sessions_trace_id", "agent_run_sessions",
                    ["trace_id"])
    # Existing links, so no membership answer changes on an upgraded install.
    # `DISTINCT` because two `agent_runs` rows may already name one child and
    # the pair constraint would fail the migration on a DB that is merely
    # untidy.
    op.execute("""
        INSERT INTO agent_run_sessions (trace_id, cli_session_id)
        SELECT DISTINCT trace_id, cli_session_id FROM agent_runs
        WHERE cli_session_id IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index("idx_agent_run_sessions_trace_id",
                  table_name="agent_run_sessions")
    op.drop_table("agent_run_sessions")
