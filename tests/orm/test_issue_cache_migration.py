"""`0024` moves the cached issue out from under the link row.

A schema change is two edits plus a model, and the two edits can silently
disagree — which is exactly how a fresh `regin init` install and a migrated one
diverge. These pin both halves and the data that has to survive the move.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command

from lib import db_migrate

_SCHEMA_SQL = Path(__file__).resolve().parents[2] / "db" / "schema.sql"


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


@pytest.fixture
def pre_0024(tmp_db):
    """A database rolled back to the shape `0023` left."""
    command.stamp(db_migrate._config(), "0024")
    command.downgrade(db_migrate._config(), "0023")
    return tmp_db


def test_0024_backfills_snapshot_json_into_the_issue_cache(pre_0024):
    conn = sqlite3.connect(str(pre_0024))
    try:
        conn.execute(
            "INSERT INTO agent_tasks (id, title, status, source)"
            " VALUES (1, 't', 'doing', 'test')")
        conn.execute(
            "INSERT INTO agent_task_links (task_id, provider, external_key,"
            " url, snapshot_json, synced_at)"
            " VALUES (1, 'linear', 'CAI-700', 'https://x/', '{\"key\": \"a\"}',"
            " '2026-01-01 00:00:00')")
        conn.commit()
    finally:
        conn.close()

    command.upgrade(db_migrate._config(), "0024")

    conn = sqlite3.connect(str(pre_0024))
    try:
        row = conn.execute(
            "SELECT snapshot_json, synced_at FROM issue_cache"
            " WHERE external_key = 'CAI-700'").fetchone()
    finally:
        conn.close()
    assert row == ('{"key": "a"}', "2026-01-01 00:00:00")


def test_the_newest_snapshot_wins_when_two_links_hold_one_key(pre_0024):
    """Measured on the live database: one key was linked from two tasks.
    Picking either arbitrarily would resurrect the disagreement the move
    removes."""
    conn = sqlite3.connect(str(pre_0024))
    try:
        for task in (1, 2):
            conn.execute(
                "INSERT INTO agent_tasks (id, title, status, source)"
                " VALUES (?, 't', 'doing', 'test')", (task,))
        conn.execute(
            "INSERT INTO agent_task_links (task_id, provider, external_key,"
            " url, snapshot_json, synced_at) VALUES (1, 'linear', 'CAI-701',"
            " 'https://x/', '\"old\"', '2026-01-01 00:00:00')")
        conn.execute(
            "INSERT INTO agent_task_links (task_id, provider, external_key,"
            " url, snapshot_json, synced_at) VALUES (2, 'linear', 'CAI-701',"
            " 'https://x/', '\"new\"', '2026-06-01 00:00:00')")
        conn.commit()
    finally:
        conn.close()

    command.upgrade(db_migrate._config(), "0024")

    conn = sqlite3.connect(str(pre_0024))
    try:
        rows = conn.execute(
            "SELECT snapshot_json FROM issue_cache"
            " WHERE external_key = 'CAI-701'").fetchall()
    finally:
        conn.close()
    assert rows == [('"new"',)]


def test_0024_drops_agent_task_links_snapshot_json_and_synced_at(pre_0024):
    """Two writers over one fact would let the task-link card and the feature
    ledger report different stages for the same issue."""
    command.upgrade(db_migrate._config(), "0024")

    conn = sqlite3.connect(str(pre_0024))
    try:
        columns = _columns(conn, "agent_task_links")
    finally:
        conn.close()
    assert "snapshot_json" not in columns
    assert "synced_at" not in columns
    assert "sync_error" in columns


def test_schema_sql_matches_alembic_head(tmp_path, tmp_db):
    """The two edits a schema change always is, compared column for column."""
    reference = tmp_path / "reference.db"
    conn = sqlite3.connect(str(reference))
    try:
        conn.executescript(_SCHEMA_SQL.read_text())
        conn.commit()
    finally:
        conn.close()

    migrated = sqlite3.connect(str(tmp_db))
    plain = sqlite3.connect(str(reference))
    try:
        for table in ("issue_cache", "agent_task_links"):
            assert _columns(migrated, table) == _columns(plain, table), table
    finally:
        migrated.close()
        plain.close()
