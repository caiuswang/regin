"""A feature becomes a row, and its lifecycle an append-only log.

Step 1 through 4 of the migration in `docs/feature-ledger-redesign.md`: build
the new tables beside the old ones, backfill them, and re-point every foreign
key. **No old column is dropped here** — `0028` does that, once the read paths
are proved against the new shape.

The backfill itself lives in `lib/features/migrate.py` rather than inline,
because the reconciliation gate has to be able to run it: the test seeds a
0026-shaped database, records the four figures each feature reported, runs this
same function, and asserts the new derivation reports the same four — per
feature, since an aggregate can agree while two features have swapped numbers.

Two pieces of history cannot be recovered and are recorded as gaps rather than
substituted with zero: the *moments* of past rounds (the column was a counter),
and the blame state of defects whose snapshot was NULL (nobody looked at the
time). Both are queryable through `declared_by = 'sync:backfill'`.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, Sequence[str], None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "features",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("origin", sa.Text(), nullable=False,
                  server_default=sa.text("'native'")),
        sa.Column("external_ref", sa.Text()),
        sa.Column("external_url", sa.Text()),
        sa.Column("sync_error", sa.Text()),
        sa.Column("synced_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.UniqueConstraint("origin", "external_ref",
                            name="uq_features_origin_ref"),
    )
    op.create_table(
        "feature_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("feature_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("declared_by", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text()),
        sa.Column("at", sa.Text(), nullable=False,
                  server_default=sa.text("(datetime('now'))")),
        sa.ForeignKeyConstraint(["feature_id"], ["features.id"],
                                ondelete="CASCADE"),
    )
    op.create_index("idx_feature_events_feature", "feature_events",
                    ["feature_id", "id"])

    with op.batch_alter_table("agent_tasks") as batch:
        batch.add_column(sa.Column("feature_id", sa.Integer()))
    op.create_index("idx_agent_tasks_feature", "agent_tasks", ["feature_id"])

    with op.batch_alter_table("agent_task_runs") as batch:
        batch.add_column(sa.Column("params_json", sa.Text()))
        batch.add_column(sa.Column("verify_cmd", sa.Text()))
        batch.add_column(sa.Column("autorun", sa.Integer(), nullable=False,
                                   server_default=sa.text("0")))

    with op.batch_alter_table("defects") as batch:
        batch.add_column(sa.Column("feature_id", sa.Integer()))
        batch.add_column(sa.Column("blamed_event_id", sa.Integer()))
        batch.add_column(sa.Column("fix_task_id", sa.Integer()))
        # Renamed rather than added: the invariant *this records who reported
        # it, never who caused it* was a comment, and the column name is where
        # it stops being one.
        batch.alter_column("session_id", new_column_name="reported_by_trace")

    with op.batch_alter_table("session_features") as batch:
        batch.add_column(sa.Column("feature_id", sa.Integer()))

    from lib.features import migrate  # noqa: PLC0415  (needs the new tables)
    # On alembic's own connection: opening a second one from inside a DDL
    # transaction deadlocks against it on SQLite.
    migrate.backfill(op.get_bind())


def downgrade() -> None:
    with op.batch_alter_table("session_features") as batch:
        batch.drop_column("feature_id")
    with op.batch_alter_table("defects") as batch:
        batch.alter_column("reported_by_trace", new_column_name="session_id")
        batch.drop_column("fix_task_id")
        batch.drop_column("blamed_event_id")
        batch.drop_column("feature_id")
    with op.batch_alter_table("agent_task_runs") as batch:
        batch.drop_column("autorun")
        batch.drop_column("verify_cmd")
        batch.drop_column("params_json")
    op.drop_index("idx_agent_tasks_feature", table_name="agent_tasks")
    with op.batch_alter_table("agent_tasks") as batch:
        batch.drop_column("feature_id")
    op.drop_index("idx_feature_events_feature", table_name="feature_events")
    op.drop_table("feature_events")
    op.drop_table("features")
