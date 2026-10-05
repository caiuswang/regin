"""S5 — migrate + doctor: this install is not behind the schema.

The test suite builds every DB from `db/schema.sql` at head, so a live install
stamped behind head is invisible to it — the one place the stale path shows is
doctor's `schema_revision` item. So the check runs the project's own `migrate`
command against the install's real DB, then asks doctor, both in a fresh
process: an in-process `DB_PATH` is whatever the suite's fixtures made it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lib.settings import settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Prints doctor's schema finding for the DB this process resolves.
DOCTOR_SCHEMA = (
    "import json; from lib.orm.engine import DB_PATH; "
    "from lib.doctor import _schema_revision_items; "
    "print(json.dumps({'db': DB_PATH, 'items': _schema_revision_items()}))"
)


def _install_env() -> dict[str, str]:
    """The environment a user's shell has — no suite redirection of the DB."""
    env = dict(os.environ)
    for key in ("REGIN_TRACE_DB_PATH", "REGIN_DATA_DIR", "REGIN_TRACE_TEST",
                "ALEMBIC_URL"):
        env.pop(key, None)
    return env


def _run(cmd: str | list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=isinstance(cmd, str), cwd=REPO_ROOT,
                          env=_install_env(), capture_output=True, text=True,
                          timeout=300)


def _doctor_schema() -> dict:
    proc = _run([sys.executable, "-c", DOCTOR_SCHEMA])
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_migrate_leaves_no_schema_finding():
    # The DB is the main worktree's, but the revisions are this checkout's: from
    # a linked worktree, migrate would stamp the shared DB with a branch's
    # migrations that master cannot resolve.
    if settings.project_root != settings.main_worktree:
        pytest.skip("migrate runs from the main worktree, which owns the DB")
    before = _doctor_schema()
    if not Path(before["db"]).is_file():
        pytest.skip(f"no install DB at {before['db']} — `regin init` makes one")

    migrate = json.loads((REPO_ROOT / ".regin" / "commands.json").read_text())["migrate"]
    proc = _run(migrate)
    assert proc.returncode == 0, f"`{migrate}` failed:\n{proc.stderr[-2000:]}"

    after = _doctor_schema()
    findings = [item for item in after["items"] if not item["present"]]
    assert after["items"], "doctor could not resolve the alembic head"
    assert not findings, f"{after['db']} is behind after migrate: {findings}"
