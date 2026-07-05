"""session_spans.agent_id — promote the owning-agent id to a real column.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-05

``agent_id`` used to live only inside the ``attributes`` JSON blob, so the
/live roster and per-agent phase reads had to ``json_extract`` every row on
every 4s poll. This promotes it to an indexed column:

- ``ALTER TABLE session_spans ADD COLUMN agent_id TEXT`` (O(1), safe on a
  live/WAL DB — no table rebuild).
- backfill ``agent_id = json_extract(attributes,'$.agent_id')`` for rows
  that carried one.
- index ``(trace_id, agent_id)`` so the grouped roster/phase scan is served
  off the index.

The ingest write path (lib/trace/trace_service/ingest.py) and the kimi
subagent tagging pass (lib/trace/kimi_subagents.py) stamp the column going
forward. The canonical final shape lives in ``db/schema.sql``; fresh
installs run that directly and never touch this revision.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("session_spans", sa.Column("agent_id", sa.Text(), nullable=True))
    op.execute(
        "UPDATE session_spans SET agent_id = "
        "json_extract(attributes, '$.agent_id') "
        "WHERE agent_id IS NULL "
        "AND json_extract(attributes, '$.agent_id') IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_spans_trace_agent "
        "ON session_spans(trace_id, agent_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_session_spans_trace_agent")
    op.drop_column("session_spans", "agent_id")
