"""A feature declares which project it belongs to.

Nullable, and nothing backfills it. The column is a *declaration*, exactly as
the feature's attribution and its ship are: deriving it from `session_repos`,
a task's `project_id` or a suspect path would make the primary scope of the
pipeline page a guess, and a guess that looks like a fact is worse than the
gap. `NULL` is the first-class "in no project" scope, not an absence to be
filled in later.

Revision ID: 0029
Revises: 0028
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: Union[str, Sequence[str], None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("features")}
    if "project_id" not in columns:
        op.add_column("features", sa.Column("project_id", sa.Integer(),
                                            nullable=True))
    op.create_index("idx_features_project", "features", ["project_id"],
                    if_not_exists=True)


def downgrade() -> None:
    op.drop_index("idx_features_project", table_name="features",
                  if_exists=True)
    op.drop_column("features", "project_id")
