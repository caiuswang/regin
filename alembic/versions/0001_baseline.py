"""baseline — first published release schema.

Revision ID: 0001
Revises: (none)
Create Date: 2026-05-18

No-op migration that anchors the Alembic revision chain. The pre-1.0
migrations were collapsed into `db/schema.sql`; every table, column and
index they added lives there directly, and later changes land as revisions
on top of this one.

`regin init` builds a DB from `db/schema.sql` — which is kept equal to the
current head, not to this baseline — and stamps it at head itself. An
existing DB is brought up to head with `regin migrate`. Upgrading an empty
DB from here produces no schema at all, so never compare schema.sql against
an alembic-from-empty build.
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
