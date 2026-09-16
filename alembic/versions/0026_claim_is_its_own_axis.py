"""The ship claim gets its own column, off `status`.

`agent_tasks.status` was carrying two axes. It is the *task lifecycle* — a run
settling writes `done` onto it, meaning "the work finished" — and 0025 also
read `done` as `state_category=completed`, meaning "this feature shipped". Two
writers, one column, and the run lifecycle holding a pen for a claim only an
operator may make.

The live failure: a task armed and run directly settled `done`, and its feature
reported `shipped` with nobody having claimed it — the exact thing CAI-273
forbids, reached by a path 0025's grain guard did not cover.

`claimed_at` is NULL until an operator declares the feature done. Only the
done-claim and the un-claim write it, and `completions_walked_back` already
took this shape for the same reason: a declared act is recorded, never derived
from current state.

**Backfilled deliberately, and narrowly.** A row that is `done` today with no
runs of its own and no parent is one an operator did claim — the claim path was
the only way it could have got there. A `done` row that holds runs got there by
its run settling, and is left unclaimed, which is the correction.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, Sequence[str], None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch:
        batch.add_column(sa.Column("claimed_at", sa.Text()))

    # Only rows the claim path could actually have produced. A `done` task that
    # holds runs reached `done` through `complete_run`, which is the bug, so it
    # stays NULL and its feature correctly stops reading as shipped.
    op.execute(sa.text(
        "UPDATE agent_tasks SET claimed_at = updated_at"
        " WHERE status = 'done'"
        "   AND parent_id IS NULL"
        "   AND id NOT IN (SELECT DISTINCT task_id FROM agent_task_runs)"))


def downgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch:
        batch.drop_column("claimed_at")
