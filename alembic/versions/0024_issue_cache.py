"""A feature-keyed cache of the issues regin has fetched.

`agent_task_links.snapshot_json` cached the same fact keyed by the wrong
thing — a *link row* — so a feature key with no task link had no cached issue
at all while a key linked from two tasks had two. Measured on the live
database: 5 of ~15 feature keys had no link row, and one key had two.

The snapshot and its timestamp move here wholesale rather than being copied.
Two writers over one fact would let the task-link card and the feature ledger
report different stages for the same issue, which is the failure this table
exists to make impossible. `sync_error` stays on the link: it is a property of
one operator's assertion, not of the issue.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "issue_cache",
        sa.Column("provider", sa.Text(), primary_key=True, nullable=False),
        sa.Column("external_key", sa.Text(), primary_key=True, nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("synced_at", sa.Text(), nullable=False),
    )
    # Newest snapshot per (provider, key) wins: a key linked from two tasks
    # has two, and picking either arbitrarily would resurrect the disagreement
    # this table removes.
    op.execute(sa.text(
        "INSERT INTO issue_cache (provider, external_key, snapshot_json,"
        " synced_at)"
        " SELECT provider, external_key, snapshot_json, synced_at"
        " FROM agent_task_links l"
        " WHERE snapshot_json IS NOT NULL AND synced_at IS NOT NULL"
        "   AND synced_at = ("
        "     SELECT MAX(synced_at) FROM agent_task_links x"
        "     WHERE x.provider = l.provider"
        "       AND x.external_key = l.external_key"
        "       AND x.snapshot_json IS NOT NULL)"
        " GROUP BY provider, external_key"))

    with op.batch_alter_table("agent_task_links") as batch:
        batch.drop_column("snapshot_json")
        batch.drop_column("synced_at")


def downgrade() -> None:
    with op.batch_alter_table("agent_task_links") as batch:
        batch.add_column(sa.Column("snapshot_json", sa.Text()))
        batch.add_column(sa.Column("synced_at", sa.Text()))
    op.execute(sa.text(
        "UPDATE agent_task_links SET"
        " snapshot_json = (SELECT c.snapshot_json FROM issue_cache c"
        "   WHERE c.provider = agent_task_links.provider"
        "     AND c.external_key = agent_task_links.external_key),"
        " synced_at = (SELECT c.synced_at FROM issue_cache c"
        "   WHERE c.provider = agent_task_links.provider"
        "     AND c.external_key = agent_task_links.external_key)"))
    op.drop_table("issue_cache")
