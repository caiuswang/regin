"""`scripts/setup.sh` run end to end, non-interactively, in a throwaway checkout.

The checkout is a copy of this tree's tracked and unignored files, so the
script under test is the working copy. pip and npm are stubbed — the run must
not touch the network or the real venv — while every `regin` call the script
makes runs for real against the copy's own `db/` and `config/`. HOME, the CLI
link directory and all REGIN_* variables are redirected or dropped so nothing
outside tmp_path is written.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REAL_PYTHON = sys.executable


def _write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_checkout(dest: Path) -> Path:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, check=True, capture_output=True,
    ).stdout.decode().split("\0")
    for rel in filter(None, listed):
        src = REPO / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return dest


@pytest.fixture
def sandbox(tmp_path):
    checkout = _copy_checkout(tmp_path / "regin")
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "fakebin"
    _write_exe(fake_bin / "node", 'echo v22.12.0\n')
    _write_exe(fake_bin / "npm", '[ "$1" = "--version" ] && echo 10.9.0\nexit 0\n')
    _write_exe(fake_bin / "npx", 'exit 0\n')

    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("REGIN_", "_REGIN_", "CLAUDE_"))}
    env.update({
        "HOME": str(home),
        "REGIN_BIN_DIR": str(tmp_path / "linkbin"),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "PYTHON": REAL_PYTHON,
    })
    return checkout, env, tmp_path


def _stub_venv(checkout: Path) -> None:
    """A .venv whose python is the test interpreter and whose pip is a no-op."""
    _write_exe(
        checkout / ".venv" / "bin" / "python",
        'if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then exit 0; fi\n'
        f'exec "{REAL_PYTHON}" "$@"\n',
    )


def _run_setup(checkout: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "scripts/setup.sh"], cwd=checkout, env=env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300,
    )


def _regin(checkout: Path, env: dict, *args: str) -> None:
    subprocess.run(
        [str(checkout / ".venv" / "bin" / "python"), "cli/regin.py", *args],
        cwd=checkout, env=env, check=True, capture_output=True, text=True, timeout=120,
    )


def _counts(db: Path) -> dict[str, int]:
    with sqlite3.connect(db) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("features", "sessions", "users")}


def _seed(checkout: Path, env: dict) -> None:
    db = checkout / "db" / "regin.db"
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO features (title) VALUES (?)",
                         [(f"feature {i}",) for i in range(3)])
        conn.executemany(
            "INSERT INTO sessions (trace_id, started_at, last_seen) VALUES (?, ?, ?)",
            [(f"trace-{i}", "2026-01-01T00:00:00", "2026-01-01T00:00:00") for i in range(5)],
        )
    _regin(checkout, env, "users", "create", "admin", "pw-123456", "--role", "admin")
    subprocess.run(
        [str(checkout / ".venv" / "bin" / "python"), "-c",
         "from lib.auth import _get_secret; _get_secret()"],
        cwd=checkout, env=env, check=True, capture_output=True, timeout=60,
    )


def test_rerunning_setup_keeps_the_database(sandbox):
    checkout, env, _ = sandbox
    _stub_venv(checkout)

    first = _run_setup(checkout, env)
    assert first.returncode == 0, first.stdout + first.stderr
    _seed(checkout, env)
    secret = checkout / "config" / "jwt_secret.txt"
    before = _counts(checkout / "db" / "regin.db")
    secret_before = secret.read_bytes()
    assert before == {"features": 3, "sessions": 5, "users": 1}

    second = _run_setup(checkout, env)

    assert second.returncode == 0, second.stdout + second.stderr
    assert _counts(checkout / "db" / "regin.db") == before
    assert secret.read_bytes() == secret_before
    assert "already exist" in second.stdout
    assert "Already wired" in second.stdout
    assert "git pull && ./scripts/setup.sh" in second.stdout
    assert "regin uninstall" in second.stdout


def test_setup_never_forces_init_or_rebuilds():
    script = (REPO / "scripts" / "setup.sh").read_text()
    assert "init --force" not in script
    assert "regin.py rebuild" not in script
    assert "users init-db" not in script.split('if [ "$MODE_CHECK" = "standalone" ]')[1].split("elif")[0]


def test_setup_refuses_python_older_than_3_11(sandbox):
    checkout, env, tmp_path = sandbox
    old_python = tmp_path / "py310" / "python3.10"
    _write_exe(old_python, 'echo 3.10\n')
    env["PYTHON"] = str(old_python)

    result = _run_setup(checkout, env)

    assert result.returncode != 0
    assert "3.11" in result.stderr
    assert not (checkout / ".venv").exists()


def test_setup_refuses_a_venv_whose_interpreter_is_gone(sandbox):
    checkout, env, _ = sandbox
    venv_bin = checkout / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to("/nonexistent/python3.11")

    result = _run_setup(checkout, env)

    assert result.returncode != 0
    assert "Remove .venv" in result.stderr


def test_setup_refuses_unsupported_node(sandbox):
    checkout, env, tmp_path = sandbox
    _write_exe(tmp_path / "fakebin" / "node", 'echo v18.20.0\n')

    result = _run_setup(checkout, env)

    assert result.returncode != 0
    assert "v18.20.0" in result.stderr
    assert not (checkout / ".venv").exists()


def test_base_install_provides_mcp():
    project = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]
    base_names = {dep.split(">")[0].split("=")[0].split("[")[0].strip().lower()
                  for dep in project["dependencies"]}
    assert "mcp" in base_names
    assert project["requires-python"] == ">=3.11"

    imported = subprocess.run(
        [REAL_PYTHON, "-c", "import lib.memory.mcp_server"],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert "ModuleNotFoundError" not in imported.stderr
    assert imported.returncode == 0, imported.stderr
