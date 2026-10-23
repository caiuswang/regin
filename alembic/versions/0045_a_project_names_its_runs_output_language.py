"""A project names the language its task runs write in.

`repos.output_language` is free text (≤64 chars after trim); NULL means the
project defers to `settings.tasks.output_language`.

Revision ID: 0045
Revises: 0044
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: Union[str, Sequence[str], None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _have(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "output_language" not in _have("repos"):
        op.add_column("repos",
                      sa.Column("output_language", sa.Text(), nullable=True))


def downgrade() -> None:
    if "output_language" in _have("repos"):
        op.drop_column("repos", "output_language")
