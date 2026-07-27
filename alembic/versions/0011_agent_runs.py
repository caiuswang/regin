"""agent_runs — the registry of sessions regin launched itself.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-27

regin now has two session tiers. A session the user started is reachable only
through ``bridge_panes`` (tmux keystrokes); a session regin launched through
the Claude Agent SDK (``lib/agent_sdk/``) is reachable through a typed channel
in-process. Steering routes on which tier a ``trace_id`` belongs to, so that
has to be a fact in the DB rather than something inferred from spans.

This table is to the SDK tier what ``bridge_panes`` is to the tmux tier: a
canonical, mutable, one-row-per-session identity record. ``pid`` is the
``claude`` child; ``status`` tracks the run lifecycle so a crashed runner's row
is distinguishable from a live one after a server restart.

The canonical final shape lives in ``db/schema.sql``; fresh installs run that
directly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        # nullable=True keeps the emitted DDL identical to db/schema.sql's
        # `INTEGER PRIMARY KEY AUTOINCREMENT`; SQLite treats a rowid alias as
        # NOT NULL regardless, and matching exactly keeps drift checks quiet.
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True,
                  nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="starting"),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("cwd", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
    )
    op.create_index("idx_agent_runs_status", "agent_runs", ["status"])


def downgrade() -> None:
    op.drop_index("idx_agent_runs_status", table_name="agent_runs")
    op.drop_table("agent_runs")
