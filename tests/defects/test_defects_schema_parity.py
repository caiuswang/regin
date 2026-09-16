"""`db/schema.sql` and the defect revisions must describe the same tables.

A schema change is two edits that can silently disagree, which is how a fresh
`regin init` install and a migrated one diverge. This rebuilds the table the
*other* way — drop it, roll the stamp back to 0021, `alembic upgrade head` —
and compares the result column-for-column and index-for-index against the shape
`db/schema.sql` produced.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command

from lib import db_migrate

_TABLES = ("defects", "session_features")
_REVISION = "0023"
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
    """`tmp_db` (built from schema.sql) with the tables rebuilt by 0023."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        for index in ("idx_defects_feature", "uq_defects_session_symptom",
                      "idx_defects_fix_item",
                      "idx_session_features_feature"):
            conn.execute(f"DROP INDEX IF EXISTS {index}")
        for table in _TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    finally:
        conn.close()
    command.stamp(db_migrate._config(), "0021")
    # Pinned rather than `head` for the same reason as the task parity test:
    # this fixture drops only the defect tables, so a later revision's CREATE
    # TABLE would collide with one schema.sql already built.
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
