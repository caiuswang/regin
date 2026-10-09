"""A session's feature is shown as its feature line, not as a `feature-<id>` tag.

Task runs used to stamp an auto `feature-<id>` row into `session_tags`; that
writer is gone and the attribution lane is the one source. This drops the rows
it left. Only auto rows: a tag a person typed is theirs, whatever it is named.

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0039"
down_revision: Union[str, Sequence[str], None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM session_tags "
               "WHERE source = 'auto' AND tag LIKE 'feature-%'")


def downgrade() -> None:
    pass
