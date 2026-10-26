"""A comment can carry images.

`comment_images` is `defect_images` keyed to a comment, and
`comments.image_high_water` is the never-reissue mark `ImageSet` keeps on
every owner row. The owner is the comment rather than its task because a
comment's `![](image:N)` resolves only among that comment's own images.

Numbered 0048, revising 0046: 0047 is held by a revision still in flight
elsewhere, and whichever of the two lands second re-chains onto the other.

Revision ID: 0048
Revises: 0046
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: Union[str, Sequence[str], None] = "0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _comment_columns() -> set[str]:
    return {c["name"] for c in
            sa.inspect(op.get_bind()).get_columns("comments")}


def upgrade() -> None:
    if "image_high_water" not in _comment_columns():
        op.add_column("comments", sa.Column(
            "image_high_water", sa.Integer(), nullable=False,
            server_default=sa.text("0")))
    if "comment_images" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "comment_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("comment_id", sa.Integer(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("bytes", sa.LargeBinary(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"],
                                ondelete="CASCADE"),
        sa.UniqueConstraint("comment_id", "idx", name="uq_comment_images_idx"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_comment_images_comment_id", "comment_images",
                    ["comment_id"])


def downgrade() -> None:
    if "comment_images" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_index("ix_comment_images_comment_id",
                      table_name="comment_images")
        op.drop_table("comment_images")
    if "image_high_water" in _comment_columns():
        op.drop_column("comments", "image_high_water")
