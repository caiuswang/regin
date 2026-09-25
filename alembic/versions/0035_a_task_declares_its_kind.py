"""A task declares what kind of step it is.

`kind` (spec | build | fix | task) is orthogonal to `status`: the first says
what the step is, the second where its runs are. It is only ever declared —
by a feature verb, `task add --kind`, the defect fix path or `transcribe` —
so existing rows keep the default `task` rather than being guessed from their
titles.

Revision ID: 0035
Revises: 0034
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: Union[str, Sequence[str], None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    have = {c["name"] for c in
            sa.inspect(op.get_bind()).get_columns("agent_tasks")}
    if "kind" not in have:
        op.add_column("agent_tasks", sa.Column(
            "kind", sa.Text(), nullable=False, server_default=sa.text("'task'")))


def downgrade() -> None:
    have = {c["name"] for c in
            sa.inspect(op.get_bind()).get_columns("agent_tasks")}
    if "kind" in have:
        op.drop_column("agent_tasks", "kind")
