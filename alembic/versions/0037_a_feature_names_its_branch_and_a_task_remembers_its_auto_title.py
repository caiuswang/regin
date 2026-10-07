"""A feature names its integration branch; a task remembers the title regin gave it.

`features.branch` is operator-set and optional — NULL falls back to the
`f{id}/integration` convention at read time, so nothing is backfilled.

`agent_tasks.auto_title` is the title regin last generated. `title ==
auto_title` is what says the title is still regin's to recompute when defects
are linked or unlinked; a hand edit breaks the equality and freezes it. Every
existing defect-sourced task still carries the title regin wrote at create, so
it is backfilled from `title`; any other task never had a generated title.

Revision ID: 0037
Revises: 0036
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: Union[str, Sequence[str], None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _have(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "branch" not in _have("features"):
        op.add_column("features", sa.Column("branch", sa.Text(), nullable=True))
    if "auto_title" not in _have("agent_tasks"):
        op.add_column("agent_tasks",
                      sa.Column("auto_title", sa.Text(), nullable=True))
        op.execute("UPDATE agent_tasks SET auto_title = title "
                   "WHERE source = 'defect'")


def downgrade() -> None:
    if "auto_title" in _have("agent_tasks"):
        op.drop_column("agent_tasks", "auto_title")
    if "branch" in _have("features"):
        op.drop_column("features", "branch")
