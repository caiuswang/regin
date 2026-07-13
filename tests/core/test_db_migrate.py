"""Tests for the alembic-driven schema migration wiring (lib/db_migrate.py).

The autouse `tmp_db` fixture builds a DB from `db/schema.sql` (== alembic
head) and points `lib.orm.engine.DB_PATH` at it, which is also the URL
alembic's env.py resolves — so these exercise the real alembic path.
"""

from __future__ import annotations

import sqlite3

from alembic.script import ScriptDirectory

import lib.orm.engine as engine
from lib import db_migrate


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


def test_run_migrate_stamps_a_pre_wiring_db(tmp_db):
    # An unstamped (pre-wiring) schema.sql DB is enrolled, not replayed.
    assert db_migrate.run_migrate() == "stamped"
    assert _stamped_version() == _head_revision()


def test_run_migrate_upgrades_a_stamped_db_idempotently(tmp_db):
    db_migrate.stamp_head()
    assert db_migrate.run_migrate() == "upgraded"
    assert db_migrate.run_migrate() == "upgraded"  # no-op, no raise
    assert _stamped_version() == _head_revision()
