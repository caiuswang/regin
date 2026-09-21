"""`db/schema.sql` and the defect revisions must describe the same tables.

A schema change is two edits that can silently disagree, which is how a fresh
`regin init` install and a migrated one diverge. This rebuilds the tables the
*other* way — drop them, roll the stamp back, replay — and compares the result
column-for-column and index-for-index against the shape `db/schema.sql`
produced.

The replay has to reach back to 0016 rather than 0021, because `0027` alters
`agent_tasks` and `agent_task_runs`: replaying it over the tables `schema.sql`
already built in their *final* shape would try to add a column that is there
and drop one that is not.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command

from lib import db_migrate

_TABLES = ("defects", "defect_images", "session_features", "features",
           "feature_events")
_REVISION = "0034"
#: Everything else the walk from 0017 creates or alters, dropped so the replay
#: does not collide with what `schema.sql` already built.
_REPLAYED = ("api_keys", "agent_tasks", "agent_task_runs", "agent_task_deps",
             "agent_task_images", "agent_task_links", "issue_cache",
             "comments", "comment_threads", "feature_images")
_SCHEMA_SQL = Path(__file__).resolve().parents[2] / "db" / "schema.sql"


def _columns(conn, table: str) -> list[tuple]:
    """(name, type, default, pk) per column.

    `notnull` is excluded here for the same reason as the task parity test:
    `INTEGER PRIMARY KEY AUTOINCREMENT` and SQLAlchemy's `INTEGER NOT NULL` +
    `PRIMARY KEY (id)` report it differently while meaning the same thing.
    """
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [(r[1], r[2].upper(), r[4], r[5]) for r in rows]


def _not_null(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
            if r[3] and not r[5]}


def _indexes(conn, table: str) -> set[tuple]:
    """(unique, origin, columns) — names are ignored because an implicit
    UNIQUE and a named constraint produce the same auto-index."""
    out = set()
    for row in conn.execute(f"PRAGMA index_list({table})").fetchall():
        name, unique, origin = row[1], bool(row[2]), row[3]
        cols = tuple(r[2] for r in
                     conn.execute(f"PRAGMA index_info({name})").fetchall())
        out.add((unique, origin, cols))
    return out


def _shape(path) -> dict:
    conn = sqlite3.connect(str(path))
    try:
        return {t: (_columns(conn, t), _not_null(conn, t), _indexes(conn, t))
                for t in _TABLES}
    finally:
        conn.close()


@pytest.fixture
def alembic_built(tmp_db):
    """`tmp_db` (built from schema.sql) with the tables rebuilt by 0017-0033."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        for index in ("idx_defects_feature", "uq_defects_session_symptom",
                      "idx_defects_fix_item", "idx_session_features_feature",
                      "idx_feature_events_feature", "idx_agent_tasks_status",
                      "idx_agent_task_images_task", "idx_agent_tasks_project",
                      "idx_agent_tasks_created",
                      "idx_agent_tasks_status_created",
                      "idx_agent_tasks_priority", "idx_agent_tasks_feature",
                      "idx_feature_images_feature", "idx_defect_images_defect"):
            conn.execute(f"DROP INDEX IF EXISTS {index}")
        for table in _TABLES + _REPLAYED:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    finally:
        conn.close()
    command.stamp(db_migrate._config(), "0016")
    command.upgrade(db_migrate._config(), _REVISION)
    return tmp_db


def test_schema_sql_matches_the_defect_revisions(tmp_path, alembic_built):
    reference = tmp_path / "reference.db"
    conn = sqlite3.connect(str(reference))
    try:
        conn.executescript(_SCHEMA_SQL.read_text())
        conn.commit()
    finally:
        conn.close()

    assert _shape(alembic_built) == _shape(reference)
