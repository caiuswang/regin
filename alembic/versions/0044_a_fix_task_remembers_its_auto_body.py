"""A fix task remembers the body regin wrote for it.

`agent_tasks.auto_body` is the body regin last generated. `body == auto_body`
is what says the body is still regin's to re-render when defects join or
leave the task; a hand edit breaks the equality and the body is frozen.

Revision ID: 0044
Revises: 0043
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: Union[str, Sequence[str], None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _have(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "auto_body" not in _have("agent_tasks"):
        op.add_column("agent_tasks",
                      sa.Column("auto_body", sa.Text(), nullable=True))
        op.execute("UPDATE agent_tasks SET auto_body = body "
                   "WHERE source = 'defect'")


def downgrade() -> None:
    if "auto_body" in _have("agent_tasks"):
        op.drop_column("agent_tasks", "auto_body")
