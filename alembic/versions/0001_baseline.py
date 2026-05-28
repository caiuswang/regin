"""baseline — first published release schema.

Revision ID: 0001
Revises: (none)
Create Date: 2026-05-18

No-op migration that anchors the Alembic revision chain at the schema
defined by `db/schema.sql`. Fresh installs run `regin init` (which
executes `db/schema.sql`) followed by `alembic stamp head` so future
migrations have a known starting point.

The pre-1.0 migration chain (0002–0019) was collapsed into this
baseline ahead of the first published release; every table, column, and
index those revisions added now lives in `db/schema.sql` directly.
Post-1.0 schema changes land as new revisions on top of this baseline.
"""
from __future__ import annotations

from typing import Sequence, Union


revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op — the baseline represents the current db/schema.sql state."""


def downgrade() -> None:
    """No-op — there is no pre-baseline state to roll back to."""
