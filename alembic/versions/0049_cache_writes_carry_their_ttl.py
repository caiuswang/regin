"""Cache writes carry their TTL; the writer-less `context_composition` goes.

`cache_creation_1h_tokens` is the 1h-TTL share of `cache_creation_tokens` on
`turn_usage` (per turn) and `sessions` (summed): a 1h write bills at 2x input
against the 5m write's 1.25x, so pricing needs the split, not just the total.

`context_composition` never had a writer after 2026-06 and nothing reads it;
`db/schema.sql` never declared it, so only long-lived installs carry it.

Revision ID: 0049
Revises: 0048
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: Union[str, Sequence[str], None] = "0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "cache_creation_1h_tokens" not in _columns("turn_usage"):
        op.add_column("turn_usage", sa.Column(
            "cache_creation_1h_tokens", sa.Integer(), nullable=False,
            server_default=sa.text("0")))
    if "cache_creation_1h_tokens" not in _columns("sessions"):
        op.add_column("sessions", sa.Column(
            "cache_creation_1h_tokens", sa.Integer(), nullable=True))
    if "context_composition" in _tables():
        op.drop_table("context_composition")


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("cache_creation_1h_tokens")
    with op.batch_alter_table("turn_usage") as batch:
        batch.drop_column("cache_creation_1h_tokens")
