"""Rate-limit thresholds already alerted, deduped across sessions.

`rate_limit_alerts` holds one row per `(kind, resets_at, threshold)` that
`regin usage measure` has warned about. The unique constraint is the dedup:
the insert is the claim, so every session sees the same answer.

Revision ID: 0042
Revises: 0041
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: Union[str, Sequence[str], None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "rate_limit_alerts" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "rate_limit_alerts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("resets_at", sa.Text(), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.UniqueConstraint("kind", "resets_at", "threshold",
                            name="uq_rate_limit_alerts"),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    op.drop_table("rate_limit_alerts")
