"""topic_ref_digests.anchors_json — wiki-cited anchors per digested ref.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-17

Content-drift detection flagged every hash change on a topic ref as material,
and roughly half of all drift threads ever opened were dismissed as noise.
This column stores, per `(repo_id, topic_id, path)` digest row, the JSON list
of identifier tokens the topic's wiki cites that were present in the ref file
at capture time (`lib/topics/wiki_anchors.py`). Detection then treats a
hash-changed ref as material only when one of those anchors vanished.

- ``ALTER TABLE topic_ref_digests ADD COLUMN anchors_json TEXT`` (O(1), safe
  on a live/WAL DB — no table rebuild).

No backfill: NULL means "captured before anchors existed (or the topic had no
wiki)" and detection keeps the old flag-on-hash behavior for such rows; a
`regin topics digest-refs` re-run populates them. The canonical final shape
lives in ``db/schema.sql``; fresh installs run that directly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "topic_ref_digests", sa.Column("anchors_json", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("topic_ref_digests", "anchors_json")
