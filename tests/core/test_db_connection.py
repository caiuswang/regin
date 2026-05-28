"""Tests for lib.orm.engine.get_connection pragma setup.

The trace system has concurrent writers (every Claude Code hook process
POSTs spans) and concurrent readers (the Flask UI plus ad-hoc CLI
queries). In SQLite's default DELETE journal mode, these serialise — a
reader waits for any active writer and vice versa. We switch to WAL so
they can proceed in parallel. These tests pin the pragmas we expect on
every connection handed out by `get_connection()` so a future edit
can't accidentally silently revert to the blocking default.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Point lib.orm.engine at a fresh SQLite file so pragma changes aren't
    polluted by the developer's actual ~/.claude/traces/data.db."""
    import lib.orm.engine as db_module
    monkeypatch.setattr(db_module, 'DB_PATH', str(tmp_path / 'test.db'))
    return db_module


def _pragma(conn, name):
    return conn.execute(f"PRAGMA {name}").fetchone()[0]


def test_connection_enables_wal_journal_mode(fresh_db):
    conn = fresh_db.get_connection()
    try:
        assert _pragma(conn, 'journal_mode') == 'wal'
    finally:
        conn.close()


def test_connection_enables_foreign_keys(fresh_db):
    """Regression: the previous connection helper already set this; we
    don't want the WAL change to accidentally remove the FK guard."""
    conn = fresh_db.get_connection()
    try:
        # SQLite returns 1 / 0 for PRAGMA foreign_keys.
        assert _pragma(conn, 'foreign_keys') == 1
    finally:
        conn.close()


def test_connection_uses_synchronous_normal(fresh_db):
    """PRAGMA synchronous values: 0=OFF 1=NORMAL 2=FULL 3=EXTRA.
    NORMAL is the SQLite-recommended default under WAL — still ACID
    across crashes, drops the per-commit fsync of the WAL."""
    conn = fresh_db.get_connection()
    try:
        assert _pragma(conn, 'synchronous') == 1  # NORMAL
    finally:
        conn.close()


def test_connection_sets_busy_timeout(fresh_db):
    """5 000 ms absorbs the occasional checkpoint-vs-reader contention
    spike so hooks don't see spurious BUSY errors that would burn their
    retry budget."""
    conn = fresh_db.get_connection()
    try:
        assert _pragma(conn, 'busy_timeout') == 5000
    finally:
        conn.close()


def test_wal_mode_persists_across_connections(fresh_db):
    """SQLite stores the journal mode in the DB header, so new
    connections inherit WAL automatically after the first one sets it.
    A raw sqlite3.connect that does NOT set pragmas should still read
    back journal_mode == 'wal'."""
    # First connection sets WAL.
    conn1 = fresh_db.get_connection()
    try:
        # Create anything so the DB gets a header written.
        conn1.execute("CREATE TABLE smoke (id INTEGER)")
        conn1.commit()
    finally:
        conn1.close()
    # Second raw connection (no pragmas applied by us) must inherit WAL.
    raw = sqlite3.connect(fresh_db.DB_PATH)
    try:
        assert _pragma(raw, 'journal_mode') == 'wal'
    finally:
        raw.close()


def test_concurrent_reader_sees_snapshot_during_writer(fresh_db):
    """The behavioural headline: in WAL, a long-running writer does not
    block a reader. Before this change, the reader would either wait
    for the writer's commit or raise `database is locked`."""
    # Writer holds an uncommitted transaction.
    writer = fresh_db.get_connection()
    writer.execute("CREATE TABLE t (v INTEGER)")
    writer.commit()
    writer.execute("INSERT INTO t (v) VALUES (1)")
    writer.commit()
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO t (v) VALUES (2)")
    # NOTE: writer has NOT committed yet.

    reader = fresh_db.get_connection()
    try:
        # Reader sees the pre-transaction snapshot (value=1). The
        # uncommitted insert is invisible.
        rows = reader.execute("SELECT v FROM t ORDER BY v").fetchall()
        assert [r[0] for r in rows] == [1]
    finally:
        reader.close()
        writer.rollback()
        writer.close()


# ── init_db ──────────────────────────────────────────────────

def test_init_db_creates_tables_from_schema(tmp_path, monkeypatch):
    """init_db reads SCHEMA_PATH and applies it — the resulting DB must
    have the core tables the rest of the app assumes."""
    import lib.orm.engine as db_module

    db_file = tmp_path / "subdir" / "test.db"
    monkeypatch.setattr(db_module, "DB_PATH", str(db_file))

    # SCHEMA_PATH is already the repo schema — reuse it via a
    # minimal inline schema so the test is hermetic.
    schema_file = tmp_path / "schema.sql"
    schema_file.write_text("""
        CREATE TABLE repos (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE pattern_docs (id INTEGER PRIMARY KEY, slug TEXT);
    """)
    monkeypatch.setattr(db_module, "SCHEMA_PATH", str(schema_file))

    db_module.init_db()

    # Parent directory created.
    assert db_file.exists()

    # Tables created.
    conn = sqlite3.connect(str(db_file))
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "repos" in names
        assert "pattern_docs" in names
    finally:
        conn.close()


def test_init_db_real_schema_does_not_seed_tags(tmp_path, monkeypatch):
    import lib.orm.engine as db_module

    db_file = tmp_path / "real-schema.db"
    schema_file = (
        Path(__file__).resolve().parent.parent.parent / "db" / "schema.sql"
    )
    monkeypatch.setattr(db_module, "DB_PATH", str(db_file))
    monkeypatch.setattr(db_module, "SCHEMA_PATH", str(schema_file))

    db_module.init_db()

    conn = sqlite3.connect(str(db_file))
    try:
        count = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


# ── db_exists ────────────────────────────────────────────────

def test_db_exists_false_when_file_missing(tmp_path, monkeypatch):
    import lib.orm.engine as db_module
    monkeypatch.setattr(db_module, "DB_PATH", str(tmp_path / "nope.db"))
    assert db_module.db_exists() is False


def test_db_exists_false_when_file_has_no_tables(tmp_path, monkeypatch):
    """An empty DB file (no schema applied) must report as not-existing."""
    import lib.orm.engine as db_module
    db_file = tmp_path / "empty.db"
    db_file.write_bytes(b"")  # truly empty file
    monkeypatch.setattr(db_module, "DB_PATH", str(db_file))
    assert db_module.db_exists() is False


def test_db_exists_true_after_init(tmp_path, monkeypatch):
    import lib.orm.engine as db_module
    db_file = tmp_path / "init.db"
    monkeypatch.setattr(db_module, "DB_PATH", str(db_file))

    # Minimal schema matching the probe query.
    schema_file = tmp_path / "schema.sql"
    schema_file.write_text("CREATE TABLE repos (id INTEGER PRIMARY KEY);")
    monkeypatch.setattr(db_module, "SCHEMA_PATH", str(schema_file))

    db_module.init_db()
    assert db_module.db_exists() is True


def test_db_exists_false_when_repos_table_absent(
        tmp_path, monkeypatch):
    """db_exists probes specifically for the `repos` table — a DB with
    only other tables should report False."""
    import lib.orm.engine as db_module
    db_file = tmp_path / "partial.db"
    monkeypatch.setattr(db_module, "DB_PATH", str(db_file))

    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    assert db_module.db_exists() is False
