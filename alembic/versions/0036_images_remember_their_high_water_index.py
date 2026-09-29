"""An image owner remembers the highest `idx` it ever issued.

`image_high_water` lives on the owning row (features, agent_tasks, defects)
because the image tables' rows are deleted on detach and so cannot remember a
freed index. Existing rows are backfilled from their surviving images.

Revision ID: 0036
Revises: 0035
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: Union[str, Sequence[str], None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OWNERS = (("features", "feature_images", "feature_id"),
           ("agent_tasks", "agent_task_images", "task_id"),
           ("defects", "defect_images", "defect_id"))


def upgrade() -> None:
    for owner, images, fk in _OWNERS:
        have = {c["name"] for c in
                sa.inspect(op.get_bind()).get_columns(owner)}
        if "image_high_water" in have:
            continue
        op.add_column(owner, sa.Column(
            "image_high_water", sa.Integer(), nullable=False,
            server_default=sa.text("0")))
        op.execute(
            f"UPDATE {owner} SET image_high_water = COALESCE("
            f"(SELECT MAX(idx) FROM {images} WHERE {images}.{fk} = "
            f"{owner}.id), 0)")


def downgrade() -> None:
    for owner, _images, _fk in _OWNERS:
        have = {c["name"] for c in
                sa.inspect(op.get_bind()).get_columns(owner)}
        if "image_high_water" in have:
            op.drop_column(owner, "image_high_water")
