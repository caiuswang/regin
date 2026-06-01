"""release schema update — session_spans.source capture-origin axis.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-01

Adds ``session_spans.source`` — which capture source wrote the row:
'hook' (live hook events: tool timing, permissions, skill reads, the
in-flight prompt placeholder) or 'transcript' (the transcript scan:
prompt anchors, assistant_response/thinking, local commands).

This lands alongside the capture refactor that makes ``session_spans``
append-only: the two sources no longer mutate each other's rows at
ingest time; both coexist and a pure serve-time merge
(``lib/trace/merge.py``) selects winners. ``source`` is the audit/debug
discriminator for that split (the merge itself keys on span_id/name, so
it is not load-bearing for correctness).

Backfill: rows minted by the transcript scan carry deterministic id
prefixes (``prompt-``, ``resp-``, ``think-``, ``cmd-``) → 'transcript';
everything else defaults to 'hook'.

The canonical final shape lives in ``db/schema.sql``; fresh installs run
that directly and never touch this revision.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add ``session_spans.source`` and backfill transcript-derived rows."""
    op.add_column(
        "session_spans",
        sa.Column("source", sa.Text(), nullable=False, server_default="hook"),
    )
    op.execute(
        "UPDATE session_spans SET source='transcript' WHERE "
        "span_id LIKE 'prompt-%' OR span_id LIKE 'resp-%' OR "
        "span_id LIKE 'think-%' OR span_id LIKE 'cmd-%'"
    )


def downgrade() -> None:
    """Drop the ``source`` column."""
    op.drop_column("session_spans", "source")
