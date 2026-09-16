"""bridge_attachments — images staged by a web composer before their prompt exists.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-01

``prompt_images`` is keyed ``(trace_id, prompt_span_id, idx)``: it is the
settled record of an image that reached a prompt, and it cannot hold one that
is still being composed. An image pasted into the ``/live`` card or the
session-detail follow-up is exactly that — the bytes exist before the prompt
does, and the span id they will eventually hang off is not knowable until the
turn lands.

So the staging row carries the bytes and the ``[Image #N]`` number handed back
to the composer. That number is allocated per trace, which keeps it in the same
namespace the terminal already uses, and it travels in the delivered text as a
literal marker. When the prompt span arrives, the marker is what names the
staged row — an identifier join, not a time window — and the bytes are copied
into ``prompt_images`` against the real span. ``claimed_span_id`` records that
hand-off so a staged row is consumed exactly once.

``dispatched_sha`` is what keeps the two namespaces apart. Claude Code's own
terminal paste also numbers ``[Image #N]`` from 1 within the same trace, so a
marker on its own cannot say whether a prompt carried the composer's image or
the terminal's. The digest of the exact delivered text can, and a staged row
binds only to a span whose text matches it.

``file_path`` is set only for the tmux tier, which cannot carry bytes and is
handed a path to the materialized file instead; the SDK tier sends the image
inline and leaves it NULL.

The canonical final shape lives in ``db/schema.sql``; fresh installs run that
directly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bridge_attachments",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("bytes", sa.LargeBinary(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("dispatched_sha", sa.Text(), nullable=True),
        sa.Column("claimed_span_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
    )
    op.create_index("idx_bridge_attachments_trace", "bridge_attachments",
                    ["trace_id"])
    op.create_index("idx_bridge_attachments_trace_idx", "bridge_attachments",
                    ["trace_id", "idx"], unique=True)


def downgrade() -> None:
    op.drop_index("idx_bridge_attachments_trace_idx",
                  table_name="bridge_attachments")
    op.drop_index("idx_bridge_attachments_trace",
                  table_name="bridge_attachments")
    op.drop_table("bridge_attachments")
