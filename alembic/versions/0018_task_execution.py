"""agent_tasks execution parameters, task runs, and the task DAG.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-11

Slice 1 captured an idea; this is the join between that row and the run
`lib/agent_sdk` already knows how to drive.

``agent_tasks`` gains the execution parameters capture is forbidden to carry
(``params_json``, ``verify_cmd``, ``autorun``) — written only by the arm route,
which is user-authenticated, so a leaked capture key still cannot start an
agent.

``agent_task_runs`` is append-only, one row per attempt, joined to
``agent_runs`` by ``trace_id`` so cost, spans and grade are not duplicated.
``UNIQUE(task_id, attempt)`` is the arbiter for two concurrent starts, the way
``UNIQUE(source, external_id)`` is the arbiter for a redelivered capture.

``agent_task_deps`` is an edge table because both directions are queried (what
blocks X, what X unblocks) and acyclicity has to be enforced on insert.

The canonical final shape lives in ``db/schema.sql``; fresh installs run that
directly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_tasks", sa.Column("params_json", sa.Text()))
    op.add_column("agent_tasks", sa.Column("verify_cmd", sa.Text()))
    op.add_column("agent_tasks",
                  sa.Column("autorun", sa.Integer(), nullable=False,
                            server_default=sa.text("0")))

    op.create_table(
        "agent_task_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.Text()),
        sa.Column("attempt", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'starting'")),
        sa.Column("started_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.Column("ended_at", sa.Text()),
        sa.Column("exit_reason", sa.Text()),
        sa.Column("verify_exit_code", sa.Integer()),
        sa.UniqueConstraint("task_id", "attempt",
                            name="uq_agent_task_runs_attempt"),
    )
    op.create_index("idx_agent_task_runs_task", "agent_task_runs", ["task_id"])
    op.create_index("idx_agent_task_runs_trace", "agent_task_runs", ["trace_id"])

    op.create_table(
        "agent_task_deps",
        sa.Column("task_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("depends_on_id", sa.Integer(), primary_key=True,
                  nullable=False),
    )
    op.create_index("idx_agent_task_deps_depends_on", "agent_task_deps",
                    ["depends_on_id"])


def downgrade() -> None:
    op.drop_index("idx_agent_task_deps_depends_on", table_name="agent_task_deps")
    op.drop_table("agent_task_deps")
    op.drop_index("idx_agent_task_runs_trace", table_name="agent_task_runs")
    op.drop_index("idx_agent_task_runs_task", table_name="agent_task_runs")
    op.drop_table("agent_task_runs")
    op.drop_column("agent_tasks", "autorun")
    op.drop_column("agent_tasks", "verify_cmd")
    op.drop_column("agent_tasks", "params_json")
