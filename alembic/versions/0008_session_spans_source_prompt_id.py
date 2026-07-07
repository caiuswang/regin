"""session_spans.source_prompt_id — promote the issuing prompt id to a column.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-07

The hook envelope's ``prompt_id`` (Claude Code 2.1.195+) is stamped by
``post_tool_trace`` onto ``attributes.source_prompt_id`` on every tool span,
but was consumed by nothing. This promotes it to a real column so the
serve-time reparent ladder (lib/trace/projection.py) can value-join a tool
span to its ``prompt-<uuid>`` anchor without ``json_extract``-scanning:

- ``ALTER TABLE session_spans ADD COLUMN source_prompt_id TEXT`` (O(1), safe
  on a live/WAL DB — no table rebuild).

No backfill or index: the value stays in the ``attributes`` JSON, and the
serve-time reader falls back to it for rows written before this promotion
(mirroring ``_turn_uuid_of``). The ingest write path
(lib/trace/trace_service/ingest.py) stamps the column going forward. The
canonical final shape lives in ``db/schema.sql``; fresh installs run that
directly and never touch this revision.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "session_spans", sa.Column("source_prompt_id", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("session_spans", "source_prompt_id")
