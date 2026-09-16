"""Feature ledger — which sessions built a feature, and what escaped.

`session_features` makes a session's feature declarable, mirroring
`session_repos`: an unattributed session stays the *absence* of a row, because
nothing here infers an attribution.

On `defects`: a pointer to the tracker item that will close a defect
(`fix_provider`/`fix_external_key`, indexed for the reverse lookup), and
`blamed_against_state` — the tracker's raw state_category for the feature at
the instant blame was assigned, which is how an escape is read without ever
comparing regin's clock against the tracker's. The partial unique index dedups
unattributed reports within one session.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_features",
        sa.Column("trace_id", sa.Text(), primary_key=True, nullable=False),
        sa.Column("feature_key", sa.Text(), primary_key=True, nullable=False),
        sa.Column("is_primary", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("provenance", sa.Text(), nullable=False,
                  server_default=sa.text("'declared'")),
        sa.Column("round", sa.Integer()),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
    )
    op.create_index("idx_session_features_feature", "session_features",
                    ["feature_key"])

    with op.batch_alter_table("defects") as batch:
        batch.add_column(sa.Column("fix_provider", sa.Text()))
        batch.add_column(sa.Column("fix_external_key", sa.Text()))
        batch.add_column(sa.Column("blamed_against_state", sa.Text()))
    op.create_index("uq_defects_session_symptom", "defects",
                    ["session_id", "symptom_key"], unique=True,
                    sqlite_where=sa.text("feature_key IS NULL"))
    op.create_index("idx_defects_fix_item", "defects",
                    ["fix_provider", "fix_external_key"])


def downgrade() -> None:
    op.drop_index("idx_defects_fix_item", table_name="defects")
    op.drop_index("uq_defects_session_symptom", table_name="defects")
    with op.batch_alter_table("defects") as batch:
        batch.drop_column("blamed_against_state")
        batch.drop_column("fix_external_key")
        batch.drop_column("fix_provider")
    op.drop_index("idx_session_features_feature",
                  table_name="session_features")
    op.drop_table("session_features")
