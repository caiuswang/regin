"""`db/schema.sql` and the `0017`–`0032` revisions must describe the same tables.

A schema change is two edits that can silently disagree, which is how a fresh
`regin init` install and a migrated one diverge. This rebuilds the task tables
the *other* way — drop them, roll the stamp back to 0016, `alembic upgrade
head` — and compares the result column-for-column and index-for-index against
the shape `db/schema.sql` produced.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command

from lib import db_migrate

_TABLES = ("api_keys", "agent_tasks", "agent_task_runs", "agent_task_deps",
           "agent_task_images", "feature_images", "project_stages",
           "feature_links", "agent_task_schedules",
           "agent_task_schedule_fires")
_SCHEMA_SQL = Path(__file__).resolve().parents[2] / "db" / "schema.sql"
_LAST_TASK_REVISION = "0046"
#: Tables the revisions between 0021 and the last one create from scratch. They
#: are dropped with the task tables so the replay does not collide with what
#: `schema.sql` already built. `agent_task_links` and `issue_cache` are in the
#: list despite being absent from `schema.sql`: the replay creates them at 0021
#: and 0024 and drops them at 0028, so the walk passes through both.
_REPLAYED = ("defects", "session_features", "issue_cache", "comments",
             "comment_threads", "agent_task_links", "features",
             "feature_events", "defect_images")


def _columns(conn, table: str) -> list[tuple]:
    """(name, type, default, pk) per column.

    `notnull` is deliberately excluded: `INTEGER PRIMARY KEY AUTOINCREMENT`
    (schema.sql) and SQLAlchemy's `INTEGER NOT NULL` + `PRIMARY KEY (id)`
    report it differently while meaning the same thing in SQLite.
    """
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [(r[1], r[2].upper(), r[4], r[5]) for r in rows]


def _not_null(conn, table: str) -> set[str]:
    """NOT NULL columns, excluding the primary key (see `_columns`)."""
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
            if r[3] and not r[5]}


def _indexes(conn, table: str) -> set[tuple]:
    """(unique, origin, columns) per index — names are ignored because an
    implicit UNIQUE and a named constraint produce the same auto-index."""
    out = set()
    for row in conn.execute(f"PRAGMA index_list({table})").fetchall():
        name, unique, origin = row[1], bool(row[2]), row[3]
        cols = tuple(r[2] for r in
                     conn.execute(f"PRAGMA index_info({name})").fetchall())
        out.add((unique, origin, cols))
    return out


def _shape(path: str) -> dict:
    conn = sqlite3.connect(path)
    try:
        return {t: (_columns(conn, t), _not_null(conn, t), _indexes(conn, t))
                for t in _TABLES}
    finally:
        conn.close()


@pytest.fixture
def alembic_built(tmp_db, tmp_path):
    """`tmp_db` (built from schema.sql) with the task tables rebuilt by
    0017 → 0021."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        for index in ("idx_agent_tasks_status", "idx_agent_task_images_task",
                      "idx_agent_tasks_project", "idx_agent_tasks_created",
                      "idx_agent_tasks_status_created",
                      "idx_agent_tasks_priority",
                      "idx_agent_tasks_feature",
                      "idx_feature_events_feature",
                      "idx_feature_images_feature",
                      "idx_defect_images_defect", "idx_defects_triage",
                      "idx_feature_links_other"):
            conn.execute(f"DROP INDEX IF EXISTS {index}")
        for table in _TABLES + _REPLAYED:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    finally:
        conn.close()
    command.stamp(db_migrate._config(), "0016")
    # Pinned to the last revision that touches a task table, not `head`:
    # upgrading past it replays a revision whose CREATE TABLE collides with
    # the one schema.sql already built.
    command.upgrade(db_migrate._config(), _LAST_TASK_REVISION)
    return tmp_db


def test_schema_sql_matches_the_task_revisions(tmp_path, alembic_built):
    reference = tmp_path / "reference.db"
    conn = sqlite3.connect(str(reference))
    try:
        conn.executescript(_SCHEMA_SQL.read_text())
        conn.commit()
    finally:
        conn.close()

    assert _shape(str(alembic_built)) == _shape(str(reference))


def test_the_new_tables_exist_on_a_fresh_schema_sql_build(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()
    assert set(_TABLES) <= names
