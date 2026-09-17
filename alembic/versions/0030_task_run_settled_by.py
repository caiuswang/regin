"""A task run records who settled it.

Two paths settle an attempt and they carry different authority. The SDK
callback settling a run says only *this session ended* — an inference, and a
wrong one whenever the session was aborted and later resumed by hand. The
agent's own `regin task finish` says *this work is done*, which is the fact the
dependency chain waits on. `ended_at` alone made the two indistinguishable, so
the inference won by arriving first and the later report was dropped.

Nullable: a row still running has no settler, and pre-existing settled rows
have one nobody recorded. NULL therefore reads as "not settled by the agent",
which is the answer that keeps the reopen rule safe on old rows.

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: Union[str, Sequence[str], None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {c["name"]
               for c in sa.inspect(op.get_bind()).get_columns("agent_task_runs")}
    if "settled_by" not in columns:
        op.add_column("agent_task_runs",
                      sa.Column("settled_by", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_task_runs", "settled_by")
