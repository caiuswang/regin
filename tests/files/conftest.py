"""Seeds for /api/files: a registered git repo, a traced session with a cwd
and file-touching spans, and the three role tokens."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from sqlmodel import text

from lib.auth import create_token
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}
ADMIN = {"Authorization": f"Bearer {create_token(1, 'ad', 'admin')}"}

_GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          env=_GIT_ENV, capture_output=True).stdout.decode()


@pytest.fixture
def repo(tmp_path):
    """A git repo with `f.py` committed, registered as a project."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "f.py").write_text("print('committed')\n")
    git(root, "add", "f.py")
    git(root, "commit", "-q", "-m", "init")
    with SessionLocal() as session:
        row = Repo(name="files-test", path=str(root))
        session.add(row)
        session.commit()
        session.refresh(row)
    return row.id, root


@pytest.fixture
def traced_session():
    """`make(trace_id, cwd, spans=[(span_id, name, attrs), ...])`."""
    def _make(trace_id: str, cwd: Path | None, spans=()):
        with SessionLocal() as session:
            session.exec(text(
                "INSERT INTO sessions (trace_id, started_at, last_seen, cwd) "
                "VALUES (:t, '2026-10-05T00:00:00Z', '2026-10-05T00:00:00Z', :c)"
            ), params={"t": trace_id, "c": str(cwd) if cwd else None})
            for span_id, name, attrs in spans:
                session.exec(text(
                    "INSERT INTO session_spans (trace_id, span_id, name, "
                    "start_time, attributes) VALUES (:t, :s, :n, "
                    "'2026-10-05T00:00:01Z', :a)"
                ), params={"t": trace_id, "s": span_id, "n": name,
                           "a": json.dumps(attrs)})
            session.commit()
    return _make
