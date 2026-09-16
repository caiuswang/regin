"""regin's own tasks as a tracker: the feature grain, and generic comments.

Three columns on `agent_tasks` and two new tables. The columns give a task the
two grains a feature needs — `parent_id` makes a task a run of something, and
`completions_walked_back` records the one event a status column cannot hold,
because a walked-back completion is not recoverable from current state.

`assignee` and `parent_id` carry no foreign key, the same provenance-label
discipline `created_by` and `project_id` already state: a deleted row must not
cascade away the tasks that outlive it.

`comment_threads.subject_id` is TEXT because a subject id is opaque to this
table, and no FK points at the subject for the same reason. `comments`
cascades from its thread, which is the only containment that is real.

Nothing is backfilled. `completions_walked_back` ships at 0 for every row
because regin records no declared act to read a past one from, and inferring
it from run timestamps is the clock heuristic the whole feature refuses.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, Sequence[str], None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch:
        batch.add_column(sa.Column("parent_id", sa.Integer()))
        batch.add_column(sa.Column("assignee", sa.Integer()))
        batch.add_column(sa.Column("completions_walked_back", sa.Integer(),
                                   nullable=False,
                                   server_default=sa.text("0")))
    op.create_index("idx_agent_tasks_parent", "agent_tasks", ["parent_id"])

    op.create_table(
        "comment_threads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subject_kind", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("resolution_state", sa.Text(), nullable=False,
                  server_default="open"),
        sa.Column("created_by", sa.Integer()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_comment_threads_subject", "comment_threads",
                    ["subject_kind", "subject_id"])

    op.create_table(
        "comments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("author_kind", sa.Text(), nullable=False,
                  server_default="user"),
        sa.Column("author_id", sa.Integer()),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["comment_threads.id"],
                                ondelete="CASCADE"),
    )
    op.create_index("ix_comments_thread", "comments", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_comments_thread", table_name="comments")
    op.drop_table("comments")
    op.drop_index("ix_comment_threads_subject", table_name="comment_threads")
    op.drop_table("comment_threads")
    op.drop_index("idx_agent_tasks_parent", table_name="agent_tasks")
    with op.batch_alter_table("agent_tasks") as batch:
        batch.drop_column("completions_walked_back")
        batch.drop_column("assignee")
        batch.drop_column("parent_id")
