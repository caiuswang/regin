"""A feature can carry images.

A task could hold a pasted mockup and deliver it with every attempt; the
feature the task was sliced out of could not, so the picture that motivated
the work had to be re-pasted onto each slice or never reached an agent at all.

`feature_images` is `agent_task_images` keyed to a feature. The delivery is
resolved at launch through the task's `feature_id` rather than copied onto the
slices at transcription, so replacing the mockup corrects the next attempt of
every slice already cut.

Revision ID: 0032
Revises: 0031
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, Sequence[str], None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "feature_images" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "feature_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("feature_id", sa.Integer(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("bytes", sa.LargeBinary(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.UniqueConstraint("feature_id", "idx",
                            name="uq_feature_images_idx"),
    )
    op.create_index("idx_feature_images_feature", "feature_images",
                    ["feature_id"])


def downgrade() -> None:
    op.drop_index("idx_feature_images_feature", table_name="feature_images")
    op.drop_table("feature_images")
