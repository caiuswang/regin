"""S6 — migrate + doctor on a copy of this install's DB.

The suite builds every DB from `db/schema.sql` at head, so a live install
stamped behind head is invisible to it. The check copies the install DB with
SQLite's online backup, points `REGIN_TRACE_DB_PATH` at the copy, and runs the
project's own `migrate` command and doctor's schema check in fresh processes —
so `0040_drop_verify_cmd_add_feature_links` is exercised on real data without
stamping the shared DB, which master could then not resolve.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from lib.settings import settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

DOCTOR_SCHEMA = (
    "import json; from lib.orm.engine import DB_PATH; "
    "from lib.doctor import _schema_revision_items; "
    "print(json.dumps({'db': DB_PATH, 'items': _schema_revision_items()}))"
)


def _env(db: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("REGIN_DATA_DIR", "REGIN_TRACE_TEST", "ALEMBIC_URL"):
        env.pop(key, None)
    env["REGIN_TRACE_DB_PATH"] = str(db)
    return env


def _run(cmd: str | list[str], db: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=isinstance(cmd, str), cwd=REPO_ROOT,
                          env=_env(db), capture_output=True, text=True,
                          timeout=600)


def _backup(src: Path, dst: Path) -> None:
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _columns(db: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def _count(db: Path, table: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _assert_doctor_clean(copy: Path) -> None:
    doctor = _run([sys.executable, "-c", DOCTOR_SCHEMA], copy)
    assert doctor.returncode == 0, doctor.stderr
    after = json.loads(doctor.stdout.strip().splitlines()[-1])
    assert after["db"] == str(copy)
    findings = [item for item in after["items"] if not item["present"]]
    assert after["items"], "doctor could not resolve the alembic head"
    assert not findings, f"{copy} is behind after migrate: {findings}"


def test_migrate_leaves_no_schema_finding(tmp_path):
    install_db = Path(settings.main_worktree) / "db" / "regin.db"
    if not install_db.is_file():
        pytest.skip(f"no install DB at {install_db} — `regin init` makes one")
    copy = tmp_path / "regin.db"
    _backup(install_db, copy)
    runs_before = _count(copy, "agent_task_runs")

    migrate = json.loads((REPO_ROOT / ".regin" / "commands.json").read_text())["migrate"]
    proc = _run(migrate, copy)
    assert proc.returncode == 0, f"`{migrate}` failed:\n{proc.stderr[-2000:]}"

    _assert_doctor_clean(copy)
    assert not {"verify_cmd", "verify_exit_code"} & _columns(copy, "agent_task_runs")
    assert _count(copy, "agent_task_runs") == runs_before
    assert {"feature_id", "other_id", "kind", "created_at"} <= _columns(copy, "feature_links")
