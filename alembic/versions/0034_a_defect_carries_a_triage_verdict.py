"""A defect carries the triage queue's verdict on it.

Three columns for one axis that is orthogonal to `status`: `status` answers
*is this bug still real*, `triage_state` answers *has anyone routed it yet*.
Folding the second into the first would put two axes on one column — a
declined defect and a fixed defect are both `closed`, and only one of them was
ever looked at.

`activity_seq` counts writes to the row and `snooze_watermark` remembers its
value at the instant of a snooze. The pair is what ends a snooze — an
inequality between two counters, with no clock anywhere in it. `updated_at`
could not do this job: it has second resolution, so two writes inside one
second are indistinguishable, and "did anything happen" would answer no while
something had.

Revision ID: 0034
Revises: 0033
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: Union[str, Sequence[str], None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("triage_state", sa.Text(), True, None),
    ("triage_reason", sa.Text(), True, None),
    ("snooze_watermark", sa.Integer(), True, None),
    ("activity_seq", sa.Integer(), False, sa.text("0")),
)


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("defects")}
    for name, type_, nullable, default in _COLUMNS:
        if name not in have:
            op.add_column("defects", sa.Column(name, type_, nullable=nullable,
                                               server_default=default))
    op.create_index("idx_defects_triage", "defects", ["triage_state"],
                    if_not_exists=True)


def downgrade() -> None:
    op.drop_index("idx_defects_triage", table_name="defects", if_exists=True)
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("defects")}
    for name, *_ in _COLUMNS:
        if name in have:
            op.drop_column("defects", name)
