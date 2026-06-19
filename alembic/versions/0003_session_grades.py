"""session_grades — post-hoc rubric grades (lib/grader).

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-12

One new table: ``session_grades``, the append-only store for the session
grader's two never-fused axes (correctness, process). Re-grading inserts
a new row; readers take the latest row per (trace_id, axis).

The canonical final shape lives in ``db/schema.sql``; fresh installs run
that directly and never touch this revision. The web app and the grader
store also self-heal an older DB with CREATE TABLE IF NOT EXISTS, so this
revision keeps Alembic-managed deployments and autogenerate's metadata
diff in step rather than being the sole upgrade path.
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS session_grades (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id        TEXT NOT NULL,
            axis            TEXT NOT NULL,
            verdict         TEXT NOT NULL,
            tier            TEXT NOT NULL DEFAULT 'screen',
            scoreboard      TEXT NOT NULL DEFAULT '{}',
            report          TEXT NOT NULL DEFAULT '',
            detail          TEXT NOT NULL DEFAULT '{}',
            rubric_version  TEXT,
            judge           TEXT,
            is_test         INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_session_grades_trace "
               "ON session_grades(trace_id, axis)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_session_grades_created "
               "ON session_grades(created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS session_grades")
