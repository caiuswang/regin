"""api_keys + agent_tasks — the capture surface's credential and its rows.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-06

``api_keys`` is a principal regin did not have: a machine credential that is
resolved by the SHA-256 of a high-entropy secret and never becomes a user
session. Only the hash is stored, so the column is unique — it is the sole
lookup path — and the plaintext exists exactly once, in the creation response.

``agent_tasks`` is the intent layer in front of the SDK launcher. It carries no
execution parameter by construction (no ``cwd``, no ``model``, no
``permission_mode``), because capture is reachable by a third-party key and the
worst a leaked one may do is add a backlog row.

``UNIQUE(source, external_id)`` is the idempotency guarantee: every webhook
provider redelivers, and identity resolves a replay where a time window would
only paper over it. NULL external ids do not collide in a unique index, so
hand-typed captures coexist freely.

The canonical final shape lives in ``db/schema.sql``; fresh installs run that
directly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=False,
                  server_default=sa.text("'[]'")),
        sa.Column("created_by", sa.Integer()),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.Column("last_used_at", sa.Text()),
        sa.Column("revoked_at", sa.Text()),
    )

    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'idea'")),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text()),
        sa.Column("tags_json", sa.Text()),
        sa.Column("links_json", sa.Text()),
        sa.Column("created_by", sa.Integer()),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.UniqueConstraint("source", "external_id",
                            name="uq_agent_tasks_source_external"),
    )
    op.create_index("idx_agent_tasks_status", "agent_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("idx_agent_tasks_status", table_name="agent_tasks")
    op.drop_table("agent_tasks")
    op.drop_table("api_keys")
