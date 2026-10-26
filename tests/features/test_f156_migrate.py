"""S9 — migrate + doctor on a copy of this install's DB.

The suite builds every DB from `db/schema.sql` at head, so a live install
stamped behind head is invisible to it. The check copies the install DB with
SQLite's online backup, points `REGIN_TRACE_DB_PATH` at the copy, and runs the
project's own `migrate` command and doctor's schema check in fresh processes —
so `0041_integer_keys_autoincrement` rebuilds real tables without stamping the
shared DB, which master could then not resolve.
"""

from __future__ import annotations

import json
import os
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

DOCTOR_SCHEMA = (
    "import json; from lib.orm.engine import DB_PATH; "
    "from lib.doctor import _schema_revision_items; "
    "print(json.dumps({'db': DB_PATH, 'items': _schema_revision_items()}))"
)

#: The tables 0041 rebuilds with AUTOINCREMENT.
REBUILT = ("agent_runs", "agent_run_sessions", "api_keys", "agent_task_runs",
           "agent_task_images", "comment_threads", "comments", "features",
           "feature_events", "feature_images", "defect_images", "feature_links")


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


def _tables(db: Path) -> dict[str, tuple[str, int]]:
    """Table name → (CREATE sql, row count) for the 0041 tables present."""
    conn = sqlite3.connect(db)
    try:
        out = {}
        for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table'"):
            if name in REBUILT:
                out[name] = (sql, conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
        return out
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


def test_migrate_leaves_no_schema_finding(install_db_copy):
    copy = install_db_copy
    before = _tables(copy)

    migrate = json.loads((REPO_ROOT / ".regin" / "commands.json").read_text())["migrate"]
    # A worktree has no .venv of its own; the interpreter running this suite is
    # the project's.
    migrate = migrate.replace(".venv/bin/python", shlex.quote(sys.executable), 1)
    proc = _run(migrate, copy)
    assert proc.returncode == 0, f"`{migrate}` failed:\n{proc.stderr[-2000:]}"

    _assert_doctor_clean(copy)
    after = _tables(copy)
    assert set(after) == set(before)
    lacking = [t for t, (sql, _) in after.items() if "AUTOINCREMENT" not in sql.upper()]
    assert not lacking, f"integer keys still reuse ids after migrate: {lacking}"
    assert {t: n for t, (_, n) in after.items()} == {t: n for t, (_, n) in before.items()}
