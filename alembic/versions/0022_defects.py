"""Defect records — what a feature got wrong, and how many rounds it took.

`defects` is its own table rather than a `kind='defect'` memory because a
defect carries lifecycle axes a memory has none of: open/closed, a round
counter, and whether it has been attributed to a feature yet. A NULL
`feature_key` is *unattributed* — a first-class state, since a wrong parent
corrupts two features' counts at once.

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, Sequence[str], None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "defects",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("feature_key", sa.Text()),
        sa.Column("symptom", sa.Text(), nullable=False),
        sa.Column("symptom_key", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text()),
        sa.Column("suspect_paths_json", sa.Text()),
        sa.Column("round", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'open'")),
        sa.Column("missing_check", sa.Text()),
        sa.Column("opened_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.Column("closed_at", sa.Text()),
        sa.UniqueConstraint("feature_key", "symptom_key",
                            name="uq_defects_feature_symptom"),
    )
    op.create_index("idx_defects_feature", "defects", ["feature_key"])


def downgrade() -> None:
    op.drop_index("idx_defects_feature", table_name="defects")
    op.drop_table("defects")
