"""A dependency edge says which of the two things it means.

`agent_task_deps` carried one edge shape for two intents. Sometimes a task
genuinely consumes its dependency's result; sometimes the edge exists only so
an operator can arm unrelated work and have it run one at a time. Resolved
under one rule, a failed task parks a queue that never depended on it.

`requires` waits for success; `after` waits only for the dependency to have
settled. Existing rows are `requires` because that is the rule the graph was
resolved under when they were drawn.

Revision ID: 0031
Revises: 0030
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, Sequence[str], None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {c["name"]
               for c in sa.inspect(op.get_bind()).get_columns("agent_task_deps")}
    if "kind" not in columns:
        op.add_column("agent_task_deps",
                      sa.Column("kind", sa.Text(), nullable=False,
                                server_default="requires"))


def downgrade() -> None:
    op.drop_column("agent_task_deps", "kind")
