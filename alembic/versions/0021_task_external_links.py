"""Link a task to an issue in a third-party tracker.

`agent_task_links` is the join between a regin task and the project-management
platform a team already runs (Linear first). One row per (task, provider,
issue); `snapshot_json` caches the normalized issue the provider last
returned, and the UNIQUE constraint is what makes re-attaching the same issue
idempotent rather than a duplicate card.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_task_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_key", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text()),
        sa.Column("synced_at", sa.Text()),
        sa.Column("sync_error", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.UniqueConstraint("task_id", "provider", "external_key",
                            name="uq_agent_task_links_ref"),
    )
    op.create_index("idx_agent_task_links_task", "agent_task_links",
                    ["task_id"])


def downgrade() -> None:
    op.drop_index("idx_agent_task_links_task", table_name="agent_task_links")
    op.drop_table("agent_task_links")
