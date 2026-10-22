"""Workspaces: a root directory grouping the registered repos beneath it.

Membership is derived from `repos.path` at read time, so there is no member
table — a repo registered later under the root joins without a write here.

Revision ID: 0043
Revises: 0042
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: Union[str, Sequence[str], None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "workspaces" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("root_path", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    op.drop_table("workspaces")
