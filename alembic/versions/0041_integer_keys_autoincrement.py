"""Integer primary keys never reuse a deleted row's id.

`op.create_table` with an integer primary key spells it `INTEGER NOT NULL …
PRIMARY KEY (id)` — a rowid alias without AUTOINCREMENT, so SQLite hands a
deleted row's id to the next insert. `db/schema.sql` has always spelled these
tables `INTEGER PRIMARY KEY AUTOINCREMENT`, so a fresh install never reuses an
id while a migrated one does. Each table is rebuilt with
`sqlite_autoincrement=True`, keeping its rows, indexes and foreign keys.

`agent_runs` and `bridge_messages` are reordered at the same time: their
later columns were added with ADD COLUMN, which appends, so a migrated
install's `SELECT *` disagreed with schema.sql's column order.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: Union[str, Sequence[str], None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "agent_runs", "agent_run_sessions", "api_keys", "agent_task_runs",
    "agent_task_images", "comment_threads", "comments", "features",
    "feature_events", "feature_images", "defect_images", "feature_links",
)

_COLUMN_ORDER = {
    "agent_runs": ("id", "trace_id", "status", "pid", "cwd", "model",
                   "detail", "cli_session_id", "created_at", "updated_at"),
    "bridge_messages": ("id", "trace_id", "body", "sender", "delivered",
                        "delivery_detail", "delivery_path", "is_test", "kind",
                        "state", "state_at", "created_at", "delivered_at"),
}


def _has_autoincrement(bind, table: str) -> bool:
    sql = bind.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table},
    ).scalar()
    return sql is not None and "AUTOINCREMENT" in sql.upper()


def _column_order(bind, table: str) -> tuple[str, ...]:
    return tuple(r[1] for r in bind.execute(
        sa.text(f"PRAGMA table_info('{table}')")).fetchall())


def _rebuild(table: str) -> None:
    order = _COLUMN_ORDER.get(table)
    with op.batch_alter_table(
        table,
        recreate="always",
        table_kwargs={"sqlite_autoincrement": True},
        partial_reordering=[order] if order else None,
    ):
        pass


def upgrade() -> None:
    bind = op.get_bind()
    # Dropping a parent table with foreign keys enforced would cascade-delete
    # its children's rows before the rebuilt copy is renamed into place.
    if bind.execute(sa.text("PRAGMA foreign_keys")).scalar():
        raise RuntimeError("0041 rebuilds referenced tables; run it with "
                           "PRAGMA foreign_keys=OFF")
    tables = set(sa.inspect(bind).get_table_names())
    for table in _TABLES:
        if table in tables and not _has_autoincrement(bind, table):
            _rebuild(table)
    for table, order in _COLUMN_ORDER.items():
        if table in tables and _column_order(bind, table) != order:
            _rebuild(table)


def downgrade() -> None:
    """No-op — dropping AUTOINCREMENT would only reintroduce id reuse."""
