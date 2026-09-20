"""A defect can carry images.

A bug report's whole evidence was the reporter's sentence. A screenshot is
what the sentence is usually describing, and there was nowhere to put one: the
browser form took text only, so the picture went into a chat message that the
ledger never saw and nobody could find from the row.

`defect_images` is `feature_images` keyed to a defect, with one difference —
ON DELETE CASCADE. A feature's mockup outlives the slices cut from it; a
defect's screenshot has no meaning without the report it evidences.

Revision ID: 0033
Revises: 0032
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: Union[str, Sequence[str], None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "defect_images" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "defect_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("defect_id", sa.Integer(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("bytes", sa.LargeBinary(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.ForeignKeyConstraint(["defect_id"], ["defects.id"],
                                ondelete="CASCADE"),
        sa.UniqueConstraint("defect_id", "idx", name="uq_defect_images_idx"),
    )
    op.create_index("idx_defect_images_defect", "defect_images", ["defect_id"])


def downgrade() -> None:
    op.drop_index("idx_defect_images_defect", table_name="defect_images")
    op.drop_table("defect_images")
