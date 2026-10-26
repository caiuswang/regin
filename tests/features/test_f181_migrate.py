"""S11 — migrate + doctor on a copy of this install's DB.

F181 carries one schema change (0049: `cache_creation_1h_tokens` on
`turn_usage` and `sessions`, `context_composition` dropped), so the bar is that
the project's own `migrate` exits 0 against a backup copy of the install DB,
doctor's schema check on that copy reports nothing missing, and the copy then
holds what 0049 promises. The copy keeps the run from stamping the shared DB
the main worktree uses, which runs master and cannot resolve 0049.
"""

from __future__ import annotations

import json
import os
import shlex
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


def _query(db: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _known_revisions() -> set[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    return {rev.revision for rev in script.walk_revisions()}


def _install_copy(tmp_path: Path) -> Path:
    install_db = Path(settings.main_worktree) / "db" / "regin.db"
    if not install_db.is_file():
        pytest.skip(f"no install DB at {install_db} — `regin init` makes one")
    copy = tmp_path / "regin.db"
    _backup(install_db, copy)

    # An install migrated by a revision that exists only as uncommitted work in
    # another worktree is ahead of this checkout, not behind it: no `migrate`
    # from here can resolve it, and it says nothing about this checkout's schema.
    stamp = _query(copy, "SELECT version_num FROM alembic_version")
    if stamp and stamp[0][0] not in _known_revisions():
        pytest.skip(f"install DB is stamped {stamp[0][0]!r}, a revision this "
                    "checkout does not have — migrated by work that has not landed")
    return copy


def _columns(db: Path, table: str) -> set[str]:
    return {row[1] for row in _query(db, f"PRAGMA table_info({table})")}


def _doctor_findings(db: Path) -> list[dict]:
    doctor = _run([sys.executable, "-c", DOCTOR_SCHEMA], db)
    assert doctor.returncode == 0, doctor.stderr
    after = json.loads(doctor.stdout.strip().splitlines()[-1])
    assert after["db"] == str(db)
    assert after["items"], "doctor could not resolve the alembic head"
    return [item for item in after["items"] if not item["present"]]


def _assert_0049_applied(db: Path) -> None:
    for table in ("turn_usage", "sessions"):
        assert "cache_creation_1h_tokens" in _columns(db, table), table
    assert not _query(db, "SELECT name FROM sqlite_master "
                          "WHERE name = 'context_composition'")


def test_migrate_leaves_no_schema_finding(tmp_path):
    copy = _install_copy(tmp_path)

    migrate = json.loads((REPO_ROOT / ".regin" / "commands.json").read_text())["migrate"]
    # A worktree has no .venv of its own; the interpreter running this suite is
    # the project's.
    migrate = migrate.replace(".venv/bin/python", shlex.quote(sys.executable), 1)
    proc = _run(migrate, copy)
    assert proc.returncode == 0, f"`{migrate}` failed:\n{proc.stderr[-2000:]}"

    findings = _doctor_findings(copy)
    assert not findings, f"{copy} is behind after migrate: {findings}"
    _assert_0049_applied(copy)
