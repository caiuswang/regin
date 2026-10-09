"""An attempt settles on the agent's own report; there is no verify command.

F155 retired the per-attempt shell gate: done is what `regin task finish`
says, and a feature's correctness is the done-claim → defects chain's job.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest
from alembic import command

import lib.orm.engine as engine
from lib import db_migrate
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask, AgentTaskRun, TASK_STATUS_RUNNING
from lib.tasks import deps, runs, store

_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_SQL = _ROOT / "db" / "schema.sql"
# Spelled in pieces so this file is not itself a hit.
_RETIRED = ("verify" + "_cmd", "verify" + "_exit_code", "run" + "_verify",
            "verify" + "_timeout")


def test_verify_cmd_is_gone(flask_client):
    found = subprocess.run(
        ["git", "grep", "-nE", "|".join(_RETIRED)], cwd=_ROOT,
        capture_output=True, text=True, check=False)
    assert found.returncode in (0, 1), found.stderr
    # This file is the one exception besides the revisions: its acceptance
    # name carries the identifier it proves gone.
    own = str(Path(__file__).resolve().relative_to(_ROOT))
    hits = [line for line in found.stdout.splitlines()
            if not line.startswith(("alembic/versions/", own + ":"))]
    assert hits == []

    task_id = flask_client.post("/api/tasks", json={"title": "t"}
                                ).get_json()["task"]["id"]
    resp = flask_client.post(f"/api/tasks/{task_id}/arm",
                             json={_RETIRED[0]: "pytest -q"})
    assert resp.status_code == 400
    assert resp.get_json()["field"] == _RETIRED[0]


def test_finish_settles_without_verify(tmp_db, native_task, monkeypatch):
    def _no_child(*_a, **_k):
        raise AssertionError("finish must not start a subprocess")

    monkeypatch.setattr(subprocess, "run", _no_child)
    monkeypatch.setattr(subprocess, "Popen", _no_child)
    launched = []
    monkeypatch.setattr(runs, "start_run", launched.append)

    task_id = native_task(title="a slice")
    runs.arm(task_id, params={}, autorun=0)
    run = runs.open_attempt(task_id)
    store.set_status(task_id, TASK_STATUS_RUNNING)
    dependent = native_task(title="downstream")
    deps.set_deps(dependent, [task_id])
    store.arm_task(dependent, params={}, autorun=0)

    result = runs.finish_run(run["id"], status="done", reason="did it")

    assert result["settled"] is True
    assert result["fired"] == [dependent] == launched
    with SessionLocal() as session:
        assert session.get(AgentTaskRun, run["id"]).status == "done"
        assert session.get(AgentTask, task_id).status == "done"


def _columns(conn, table: str) -> list[tuple]:
    """(name, type, notnull, default, pk). The primary key's `notnull` is
    left out: `INTEGER PRIMARY KEY AUTOINCREMENT` and SQLAlchemy's spelling
    report it differently while meaning the same thing in SQLite."""
    return [(r[1], r[2].upper(), bool(r[3]) and not r[5], r[4], r[5])
            for r in conn.execute(f"PRAGMA table_info({table})")]


def _indexes(conn, table: str) -> set[tuple]:
    out = set()
    for row in conn.execute(f"PRAGMA index_list({table})").fetchall():
        cols = tuple(r[2] for r in
                     conn.execute(f"PRAGMA index_info({row[1]})").fetchall())
        out.add((bool(row[2]), cols))
    return out


def _ddl(conn, table: str) -> str:
    return conn.execute("SELECT sql FROM sqlite_master WHERE name = ?",
                        (table,)).fetchone()[0].upper()


@pytest.fixture
def db_at_0039(tmp_db):
    """`tmp_db` rolled back to the 0039 shape, with one verify-carrying
    attempt in it."""
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute("DROP INDEX IF EXISTS idx_feature_links_other")
        conn.execute("DROP TABLE feature_links")
        conn.execute(f"ALTER TABLE agent_task_runs ADD COLUMN {_RETIRED[1]}"
                     " INTEGER")
        conn.execute(f"ALTER TABLE agent_task_runs ADD COLUMN {_RETIRED[0]}"
                     " TEXT")
        conn.execute("INSERT INTO agent_tasks (id, title, source) "
                     "VALUES (1, 'old', 'test')")
        conn.execute(
            "INSERT INTO agent_task_runs (id, task_id, attempt, status, "
            f"exit_reason, params_json, {_RETIRED[0]}, {_RETIRED[1]}) "
            "VALUES (7, 1, 1, 'failed', 'verify failed', '{}', 'pytest', 1)")
        conn.commit()
    finally:
        conn.close()
    command.stamp(db_migrate._config(), "0039")
    return tmp_db


def test_migration_0040_drops_verify_adds_links(db_at_0039, tmp_path):
    assert db_migrate.run_migrate() == "upgraded"

    reference = tmp_path / "reference.db"
    ref = sqlite3.connect(str(reference))
    ref.executescript(_SCHEMA_SQL.read_text())
    conn = sqlite3.connect(engine.DB_PATH)
    try:
        names = {c[0] for c in _columns(conn, "agent_task_runs")}
        assert not names & set(_RETIRED)
        assert conn.execute(
            "SELECT id, task_id, status, exit_reason FROM agent_task_runs"
        ).fetchall() == [(7, 1, "failed", "verify failed")]
        assert _columns(conn, "agent_task_runs") == _columns(
            ref, "agent_task_runs")
        assert _columns(conn, "feature_links") == _columns(
            ref, "feature_links")
        assert _indexes(conn, "feature_links") == _indexes(
            ref, "feature_links")
        assert "AUTOINCREMENT" in _ddl(conn, "agent_task_runs")
        assert "AUTOINCREMENT" in _ddl(ref, "agent_task_runs")
    finally:
        conn.close()
        ref.close()
