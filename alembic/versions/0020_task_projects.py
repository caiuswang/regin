"""Task project, priority, and the indexes the task feed actually orders by.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-11

Two things that only make sense together:

* ``agent_tasks.project_id`` — the grouping axis. A *project* is a registered
  repo (``repos.id``), which is what ``project_id`` already means in
  ``pattern_deployments`` and in ``web/blueprints/skills.py``. Nullable and
  with no foreign key, exactly like that column: a repo dropped from the
  registry leaves its tasks in the backlog rather than cascading them away.
* ``agent_tasks.priority`` — an ordered enum stored as text, paired with
  ``priority_rank``, its integer sort key. Two columns for one axis on purpose:
  ordering by a CASE over the words cannot use an index, and its value cannot
  be read back off a serialized row, so the keyset cursor for page two cannot
  be built. ``lib.orm.models.tasks.PRIORITY_ORDER`` is the mapping.
* the composite indexes. ``GET /api/tasks`` keyset-paginates on
  ``(created_at DESC, id DESC)`` but the only index was on ``status`` alone, so
  every page was a scan plus a sort. These are what make the feed O(log n)
  instead of O(n) once the table is no longer a demo.

The canonical final shape lives in ``db/schema.sql``; fresh installs run that
directly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0020"
down_revision: Union[str, Sequence[str], None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch:
        batch.add_column(sa.Column("project_id", sa.Integer()))
        batch.add_column(sa.Column("priority", sa.Text(), nullable=False,
                                   server_default=sa.text("'none'")))
        batch.add_column(sa.Column("priority_rank", sa.Integer(),
                                   nullable=False, server_default=sa.text("4")))

    op.create_index("idx_agent_tasks_project", "agent_tasks", ["project_id"])
    op.create_index("idx_agent_tasks_created", "agent_tasks",
                    ["created_at", "id"])
    op.create_index("idx_agent_tasks_status_created", "agent_tasks",
                    ["status", "created_at", "id"])
    op.create_index("idx_agent_tasks_priority", "agent_tasks",
                    ["priority_rank", "created_at", "id"])


def downgrade() -> None:
    op.drop_index("idx_agent_tasks_priority", table_name="agent_tasks")
    op.drop_index("idx_agent_tasks_status_created", table_name="agent_tasks")
    op.drop_index("idx_agent_tasks_created", table_name="agent_tasks")
    op.drop_index("idx_agent_tasks_project", table_name="agent_tasks")
    with op.batch_alter_table("agent_tasks") as batch:
        batch.drop_column("priority_rank")
        batch.drop_column("priority")
        batch.drop_column("project_id")
