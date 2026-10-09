"""An attempt settles on the agent's report alone; features can name each other.

`agent_task_runs.verify_cmd` / `verify_exit_code` are dropped with the code
that read them: a run is done when its agent says so, and a feature's
correctness is the done-claim → defects chain's job, not a shell command's.
Existing attempts keep every other column.

`feature_links` holds peer relations between features (`follows`, `related`).

Revision ID: 0040
Revises: 0039
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: Union[str, Sequence[str], None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DROPPED = ("verify_cmd", "verify_exit_code")


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in _inspector().get_columns(table)}


def upgrade() -> None:
    # A plain DROP COLUMN, not a batch rebuild: the rebuild re-spells the table
    # without AUTOINCREMENT, so a migrated install would reuse a deleted run's
    # id where a fresh one never does. Neither column is indexed or
    # constrained, which is all SQLite's in-place drop requires.
    for column in _DROPPED:
        if column in _columns("agent_task_runs"):
            op.execute(f"ALTER TABLE agent_task_runs DROP COLUMN {column}")
    if "feature_links" not in _inspector().get_table_names():
        op.create_table(
            "feature_links",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("feature_id", sa.Integer(),
                      sa.ForeignKey("features.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("other_id", sa.Integer(),
                      sa.ForeignKey("features.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("kind", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False,
                      server_default=sa.text("(datetime('now'))")),
            sa.UniqueConstraint("feature_id", "other_id", "kind",
                                name="uq_feature_links"),
        )
        op.create_index("idx_feature_links_other", "feature_links",
                        ["other_id"])


def downgrade() -> None:
    if "feature_links" in _inspector().get_table_names():
        op.drop_index("idx_feature_links_other", table_name="feature_links")
        op.drop_table("feature_links")
    missing = [c for c in _DROPPED if c not in _columns("agent_task_runs")]
    if missing:
        with op.batch_alter_table("agent_task_runs") as batch:
            if "verify_exit_code" in missing:
                batch.add_column(sa.Column("verify_exit_code", sa.Integer()))
            if "verify_cmd" in missing:
                batch.add_column(sa.Column("verify_cmd", sa.Text()))
