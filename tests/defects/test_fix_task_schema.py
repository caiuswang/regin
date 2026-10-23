"""`features.branch` and `agent_tasks.auto_title` (revision 0037).

A fresh install gets both columns from `db/schema.sql`; an existing install
gets them from `0037`. The two must land on the same shape, and the migration
must hand every existing defect-sourced task its current title as the one
regin generated — otherwise the first relink would treat it as hand-edited.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory

from lib import db_migrate

_SCHEMA_SQL = Path(__file__).resolve().parents[2] / "db" / "schema.sql"
_NEW = {"features": "branch", "agent_tasks": "auto_title"}


def _columns(path, table: str) -> list[tuple]:
    """(name, type, notnull, default, pk) per column, in table order."""
    conn = sqlite3.connect(str(path))
    try:
        return [(r[1], r[2].upper(), r[3], r[4], r[5]) for r in
                conn.execute(f"PRAGMA table_info({table})").fetchall()]
    finally:
        conn.close()


@pytest.fixture
def at_0036(tmp_db):
    """`tmp_db` set back to the pre-0037 shape and stamped there, holding one
    defect-sourced task, one hand-captured task and one feature."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        # 0044's and 0038's columns first: a column re-added by a later revision lands
        # last, so leaving it in place would reorder the replayed table.
        conn.execute("ALTER TABLE agent_tasks DROP COLUMN auto_body")
        conn.execute("ALTER TABLE agent_tasks DROP COLUMN stage_key")
        for table, column in _NEW.items():
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.execute("INSERT INTO features (id, title) VALUES (7, 'F')")
        conn.execute(
            "INSERT INTO agent_tasks (id, title, source, external_id) VALUES "
            "(1, 'symptom of defect 4', 'defect', 'defect:4'), "
            "(2, 'hand typed', 'web', NULL)")
        conn.commit()
    finally:
        conn.close()
    command.stamp(db_migrate._config(), "0036")
    return tmp_db


def test_features_branch_column_exists(tmp_db):
    from lib.orm import SessionLocal
    from lib.orm.models.features import Feature

    assert ("branch", "TEXT", 0, None, 0) in _columns(tmp_db, "features")
    with SessionLocal() as s:
        named, unnamed = Feature(title="F", branch="f7/integration"), \
            Feature(title="G")
        s.add_all([named, unnamed])
        s.commit()
        ids = named.id, unnamed.id
    with SessionLocal() as s:
        assert [s.get(Feature, i).branch for i in ids] == \
            ["f7/integration", None]


def test_agent_tasks_auto_title_backfilled_for_defect_tasks(at_0036):
    command.upgrade(db_migrate._config(), "head")

    conn = sqlite3.connect(str(at_0036))
    try:
        rows = dict(conn.execute(
            "SELECT id, auto_title FROM agent_tasks").fetchall())
        branch = conn.execute(
            "SELECT branch FROM features WHERE id = 7").fetchone()[0]
    finally:
        conn.close()
    assert rows == {1: "symptom of defect 4", 2: None}
    assert branch is None


def test_schema_sql_matches_alembic_head(tmp_path, at_0036):
    head = ScriptDirectory.from_config(db_migrate._config()).get_current_head()
    assert head == "0046"

    command.upgrade(db_migrate._config(), "head")
    fresh = tmp_path / "fresh.db"
    conn = sqlite3.connect(str(fresh))
    try:
        conn.executescript(_SCHEMA_SQL.read_text())
    finally:
        conn.close()

    for table in _NEW:
        assert _columns(at_0036, table) == _columns(fresh, table), table
