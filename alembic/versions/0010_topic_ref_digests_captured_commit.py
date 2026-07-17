"""topic_ref_digests.captured_commit — git baseline for drift-judge diffs.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-17

The batched drift judge needs the *old* content of a drifted ref to show the
judge what actually changed, but digests store only a hash. Stamping the repo
HEAD at capture time lets the judge reconstruct the change as
``git diff <captured_commit> -- <path>`` instead of guessing from the current
file alone.

- ``ALTER TABLE topic_ref_digests ADD COLUMN captured_commit TEXT`` (O(1),
  safe on a live/WAL DB — no table rebuild).

No backfill: NULL means "captured before commits were stamped" and the judge
simply gets no diff evidence for that row (it still sees the current file and
the wiki). Any later capture stamps it. The canonical final shape lives in
``db/schema.sql``; fresh installs run that directly.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "topic_ref_digests",
        sa.Column("captured_commit", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("topic_ref_digests", "captured_commit")
