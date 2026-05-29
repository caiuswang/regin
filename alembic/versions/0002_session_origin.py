"""session origin axis — split the overloaded agent_type.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-29

``sessions.agent_type`` used to double as both the launching agent's
vendor ('claude' | 'codex') and a marker for captured dynamic-workflow
runs (the literal 'workflow'). Those are orthogonal axes, so this
revision adds ``sessions.origin`` — what KIND of row this is
('session' for a real interactive agent session, 'workflow' for a
captured run, with room for future synthetic kinds) — and backfills the
old overloaded rows: any row tagged agent_type='workflow' becomes
origin='workflow' with agent_type reset to 'claude' (the Workflow tool
is a Claude Code feature, so the vendor is always Claude).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add ``sessions.origin`` and backfill the old workflow rows."""
    op.add_column(
        "sessions",
        sa.Column("origin", sa.Text(), server_default="session"),
    )
    op.execute(
        "UPDATE sessions SET origin='workflow', agent_type='claude' "
        "WHERE agent_type='workflow'"
    )


def downgrade() -> None:
    """Drop the ``origin`` column (the agent_type backfill is not reversed)."""
    op.drop_column("sessions", "origin")
