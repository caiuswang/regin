"""S10 — migrate + doctor on a copy of this install's DB.

F180 ships one revision (`0048`, `comment_images`), so the bar is three facts:
every F180 commit that touches a schema surface touches all three (CLAUDE.md
"Schema drift gotcha"), the project's own `migrate` exits 0 against a backup
copy of the install DB and leaves the F180 table in it, and doctor's schema
check on that copy reports nothing missing. The copy keeps the run from
stamping the shared DB the main worktree uses.
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

SCHEMA_SURFACES = ("db/schema.sql", "alembic/versions/", "lib/orm/models/")
F180_TABLE = "comment_images"


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


def _f180_paths(*extra: str) -> set[str]:
    log = subprocess.run(
        ["git", "log", "--no-merges", "--grep=^F180[ :]", "--name-only", "--format=",
         *extra],
        cwd=REPO_ROOT, capture_output=True, text=True)
    if log.returncode != 0:
        pytest.skip(f"git log unavailable: {log.stderr.strip()}")
    return {line for line in log.stdout.splitlines() if line}


def _f180_schema_paths() -> set[str]:
    paths = _f180_paths()
    if not paths:
        pytest.skip("no F180 commits in this checkout's history")
    return {p for p in paths if p.startswith(SCHEMA_SURFACES)}


def _assert_schema_edit_is_whole(paths: set[str]) -> None:
    missing = [s for s in SCHEMA_SURFACES if not any(p.startswith(s) for p in paths)]
    assert not missing, f"F180 edits {sorted(paths)} but not {missing}"


def _script():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))


def _f180_base() -> str:
    """The revision F180's chain revises — the newest one an install predating F180 holds."""
    added = _f180_paths("--diff-filter=A", "--", "alembic/versions/")
    ours = {rev.revision: rev for rev in _script().walk_revisions()
            if f"alembic/versions/{Path(rev.path).name}" in added}
    assert ours, f"F180 touches alembic/versions/ but adds no revision: {sorted(added)}"
    bases = {rev.down_revision for rev in ours.values()} - set(ours)
    assert len(bases) == 1, f"F180 revisions do not form one chain: {bases}"
    return bases.pop()


def _stamp(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def _install_copy(tmp_path: Path, base: str) -> Path:
    install_db = Path(settings.main_worktree) / "db" / "regin.db"
    if not install_db.is_file():
        pytest.skip(f"no install DB at {install_db} — `regin init` makes one")
    copy = tmp_path / "regin.db"
    _backup(install_db, copy)

    # An install migrated by a revision that exists only as uncommitted work in
    # another worktree cannot be resolved from here. When it still predates
    # F180, its schema is F180's base plus that work's additions, so rewinding
    # the copy's stamp to the base tests F180's revision against this install's
    # real data; once it carries F180's table it is ahead, and says nothing.
    conn = sqlite3.connect(copy)
    try:
        stamp = _stamp(conn)
        known = {rev.revision for rev in _script().walk_revisions()}
        if stamp is not None and stamp not in known:
            if _has_table(conn, F180_TABLE):
                pytest.skip(f"install DB is stamped {stamp!r}, a revision this "
                            "checkout does not have, and already holds F180's table")
            conn.execute("UPDATE alembic_version SET version_num = ?", (base,))
            conn.commit()
    finally:
        conn.close()
    return copy


def test_migrate_leaves_no_schema_finding(tmp_path):
    _assert_schema_edit_is_whole(_f180_schema_paths())
    copy = _install_copy(tmp_path, _f180_base())

    migrate = json.loads((REPO_ROOT / ".regin" / "commands.json").read_text())["migrate"]
    # A worktree has no .venv of its own; the interpreter running this suite is
    # the project's.
    migrate = migrate.replace(".venv/bin/python", shlex.quote(sys.executable), 1)
    proc = _run(migrate, copy)
    assert proc.returncode == 0, f"`{migrate}` failed:\n{proc.stderr[-2000:]}"

    conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
    try:
        assert _has_table(conn, F180_TABLE), f"migrate left {copy} without {F180_TABLE}"
    finally:
        conn.close()

    doctor = _run([sys.executable, "-c", DOCTOR_SCHEMA], copy)
    assert doctor.returncode == 0, doctor.stderr
    after = json.loads(doctor.stdout.strip().splitlines()[-1])
    assert after["db"] == str(copy)
    assert after["items"], "doctor could not resolve the alembic head"
    findings = [item for item in after["items"] if not item["present"]]
    assert not findings, f"{copy} is behind after migrate: {findings}"
