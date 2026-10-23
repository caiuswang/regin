"""A task can carry a schedule, and every time-side decision is a fire row.

`agent_task_schedules` is one row per task (`at` / `window` gate the armed
attempt, `cron` opens new ones); `agent_task_schedule_fires` is append-only,
and its `UNIQUE(schedule_id, slot_at)` is what lets the scheduler and the
dependency path race for one window instance with only one launching.

Revision ID: 0046
Revises: 0045
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: Union[str, Sequence[str], None] = "0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("(datetime('now'))")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    have = _tables()
    if "agent_task_schedules" not in have:
        op.create_table(
            "agent_task_schedules",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("task_id", sa.Integer(),
                      sa.ForeignKey("agent_tasks.id", ondelete="CASCADE"),
                      nullable=False, unique=True),
            sa.Column("kind", sa.Text(), nullable=False),
            sa.Column("run_at", sa.Text(), nullable=True),
            sa.Column("window_start", sa.Text(), nullable=True),
            sa.Column("window_end", sa.Text(), nullable=True),
            sa.Column("cron_expr", sa.Text(), nullable=True),
            sa.Column("tz", sa.Text(), nullable=False),
            sa.Column("enabled", sa.Integer(), nullable=False,
                      server_default=sa.text("1")),
            sa.Column("next_fire_at", sa.Text(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False,
                      server_default=_NOW),
            sa.Column("updated_at", sa.Text(), nullable=False,
                      server_default=_NOW),
            sqlite_autoincrement=True,
        )
        op.create_index("idx_agent_task_schedules_next",
                        "agent_task_schedules", ["next_fire_at"])
    if "agent_task_schedule_fires" not in have:
        op.create_table(
            "agent_task_schedule_fires",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("schedule_id", sa.Integer(),
                      sa.ForeignKey("agent_task_schedules.id",
                                    ondelete="CASCADE"),
                      nullable=False),
            sa.Column("slot_at", sa.Text(), nullable=False),
            sa.Column("fired_at", sa.Text(), nullable=False,
                      server_default=_NOW),
            sa.Column("outcome", sa.Text(), nullable=False),
            sa.Column("via", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.UniqueConstraint("schedule_id", "slot_at",
                                name="uq_agent_task_schedule_fires_slot"),
            sqlite_autoincrement=True,
        )
        op.create_index("idx_agent_task_schedule_fires_schedule",
                        "agent_task_schedule_fires", ["schedule_id"])


def downgrade() -> None:
    have = _tables()
    if "agent_task_schedule_fires" in have:
        op.drop_table("agent_task_schedule_fires")
    if "agent_task_schedules" in have:
        op.drop_table("agent_task_schedules")
