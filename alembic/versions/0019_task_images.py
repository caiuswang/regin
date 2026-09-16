"""Images attached to a captured idea.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-11

``agent_task_images`` is keyed to the task rather than to a trace, which is
what separates it from ``bridge_attachments``: a task has no trace until
someone runs it, and it may be run more than once, so the bytes are re-read on
every attempt instead of being claimed and spent by a single prompt.

The canonical final shape lives in ``db/schema.sql``; fresh installs run that
directly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_task_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("bytes", sa.LargeBinary(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.UniqueConstraint("task_id", "idx",
                            name="uq_agent_task_images_idx"),
    )
    op.create_index("idx_agent_task_images_task", "agent_task_images",
                    ["task_id"])


def downgrade() -> None:
    op.drop_index("idx_agent_task_images_task",
                  table_name="agent_task_images")
    op.drop_table("agent_task_images")
