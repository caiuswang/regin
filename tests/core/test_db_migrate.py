"""Tests for the alembic-driven schema migration wiring (lib/db_migrate.py).

The autouse `tmp_db` fixture builds a DB from `db/schema.sql` (== alembic
head) and points `lib.orm.engine.DB_PATH` at it, which is also the URL
alembic's env.py resolves — so these exercise the real alembic path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import typer
from alembic import command
from alembic.script import ScriptDirectory

import lib.orm.engine as engine
from cli.commands import db as db_cmd
from lib import db_migrate

_FIXTURE_0008 = (Path(__file__).resolve().parents[1]
                 / "fixtures" / "schema" / "schema_0008.sql")


def _head_revision() -> str:
    return ScriptDirectory.from_config(db_migrate._config()).get_current_head()


def _stamped_version() -> str | None:
    # Read DB_PATH live off the module — the autouse tmp_db fixture
    # monkeypatches it, so a top-level `from ... import DB_PATH` would bind the
    # real repo DB and read the wrong file.
    conn = sqlite3.connect(engine.DB_PATH)
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_schema_sql_db_starts_unstamped(tmp_db):
    # schema.sql does not create alembic_version — a fresh build is unenrolled.
    assert db_migrate._is_stamped() is False


def test_stamp_head_enrolls_at_head(tmp_db):
    db_migrate.stamp_head()
    assert db_migrate._is_stamped() is True
    assert _stamped_version() == _head_revision()


def _has_alembic_version() -> bool:
    conn = sqlite3.connect(engine.DB_PATH)
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='alembic_version'").fetchone() is not None
    finally:
        conn.close()


def test_migrate_stamps_an_unstamped_complete_database(tmp_db):
    db_migrate.stamp_head()
    conn = sqlite3.connect(engine.DB_PATH)
    conn.execute("DROP TABLE alembic_version")
    conn.commit()
    conn.close()

    db_cmd.cmd_migrate()

    assert _stamped_version() == _head_revision()


def test_migrate_refuses_an_unstamped_incomplete_database(tmp_db, capsys):
    conn = sqlite3.connect(engine.DB_PATH)
    conn.execute("DROP TABLE features")
    conn.execute("ALTER TABLE defects DROP COLUMN triage_reason")
    conn.commit()
    conn.close()

    with pytest.raises(typer.Exit) as exc:
        db_cmd.cmd_migrate()

    assert exc.value.exit_code != 0
    err = capsys.readouterr().err
    assert "table features" in err
    assert "column defects.triage_reason" in err
    assert "regin init --force" in err
    assert not _has_alembic_version()


def _shape(path: str) -> dict:
    """Every table's columns in order, indexes, foreign keys and AUTOINCREMENT
    flag, plus the names of all indexes and triggers — what `sqlite_master`
    says once formatting and quoting differences are normalized away."""
    conn = sqlite3.connect(path)
    try:
        tables = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
        ).fetchall()
        shape: dict = {}
        for name, sql in tables:
            # `notnull` on the primary key is skipped: `INTEGER PRIMARY KEY`
            # and SQLAlchemy's `INTEGER NOT NULL … PRIMARY KEY (id)` report
            # it differently while meaning the same thing.
            columns = [(r[1], r[2].upper(), bool(r[3]) and not r[5], r[4], r[5])
                       for r in conn.execute(f"PRAGMA table_info('{name}')")]
            indexes = sorted(
                (bool(r[2]), r[3], bool(r[4]), tuple(
                    c[2] for c in conn.execute(f"PRAGMA index_info('{r[1]}')")))
                for r in conn.execute(f"PRAGMA index_list('{name}')"))
            fks = sorted(tuple(r[2:]) for r in conn.execute(
                f"PRAGMA foreign_key_list('{name}')"))
            shape[name] = (columns, indexes, fks,
                           "AUTOINCREMENT" in (sql or "").upper())
        shape["<objects>"] = sorted(conn.execute(
            "SELECT type, name, tbl_name FROM sqlite_master "
            "WHERE type IN ('index', 'trigger', 'view') "
            "AND name NOT LIKE 'sqlite_autoindex%'").fetchall())
        return shape
    finally:
        conn.close()


def test_upgraded_schema_matches_fresh_schema(tmp_db, tmp_path):
    engine.dispose_engine()
    Path(tmp_db).unlink()
    conn = sqlite3.connect(str(tmp_db))
    conn.executescript(_FIXTURE_0008.read_text())
    conn.commit()
    conn.close()
    command.stamp(db_migrate._config(), "0008")
    # Rows written before the AUTOINCREMENT rebuild must survive it, children
    # included.
    command.upgrade(db_migrate._config(), "0040")
    conn = sqlite3.connect(str(tmp_db))
    conn.execute("INSERT INTO features (id, title) VALUES (3, 'f')")
    conn.execute("INSERT INTO feature_events (feature_id, kind, declared_by) "
                 "VALUES (3, 'created', 'test')")
    conn.commit()
    conn.close()

    db_cmd.cmd_migrate()

    fresh = tmp_path / "fresh.db"
    conn = sqlite3.connect(str(fresh))
    conn.executescript(engine.load_schema_sql())
    conn.commit()
    conn.close()
    assert _stamped_version() == _head_revision()
    assert _shape(str(tmp_db)) == _shape(str(fresh))
    conn = sqlite3.connect(str(tmp_db))
    try:
        assert conn.execute(
            "SELECT feature_id, kind FROM feature_events").fetchall() == [
            (3, "created")]
        conn.execute("DELETE FROM feature_events")
        conn.execute("DELETE FROM features")
        conn.execute("INSERT INTO features (title) VALUES ('g')")
        # AUTOINCREMENT: a deleted feature's id is never handed out again.
        assert conn.execute("SELECT id FROM features").fetchone()[0] == 4
    finally:
        conn.close()


def test_run_migrate_upgrades_a_stamped_db_idempotently(tmp_db):
    db_migrate.stamp_head()
    assert db_migrate.run_migrate() == "upgraded"
    assert db_migrate.run_migrate() == "upgraded"  # no-op, no raise
    assert _stamped_version() == _head_revision()
