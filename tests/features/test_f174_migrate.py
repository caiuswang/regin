"""S6 — migrate + doctor on a copy of this install's DB.

F174 adds no alembic revision, so the bar is that this install is not behind
head: the project's own `migrate` command exits 0 against a backup copy of the
install DB, and doctor's schema check on that copy reports nothing missing.
The copy keeps the run from stamping the shared DB the main worktree uses.
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


def test_migrate_leaves_no_schema_finding(tmp_path):
    install_db = Path(settings.main_worktree) / "db" / "regin.db"
    if not install_db.is_file():
        pytest.skip(f"no install DB at {install_db} — `regin init` makes one")
    copy = tmp_path / "regin.db"
    _backup(install_db, copy)

    migrate = json.loads((REPO_ROOT / ".regin" / "commands.json").read_text())["migrate"]
    # A worktree has no .venv of its own; the interpreter running this suite is
    # the project's.
    migrate = migrate.replace(".venv/bin/python", shlex.quote(sys.executable), 1)
    proc = _run(migrate, copy)
    assert proc.returncode == 0, f"`{migrate}` failed:\n{proc.stderr[-2000:]}"

    doctor = _run([sys.executable, "-c", DOCTOR_SCHEMA], copy)
    assert doctor.returncode == 0, doctor.stderr
    after = json.loads(doctor.stdout.strip().splitlines()[-1])
    assert after["db"] == str(copy)
    assert after["items"], "doctor could not resolve the alembic head"
    findings = [item for item in after["items"] if not item["present"]]
    assert not findings, f"{copy} is behind after migrate: {findings}"
