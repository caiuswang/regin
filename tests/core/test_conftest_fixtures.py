"""Smoke tests for the shared fixtures in tests/conftest.py.

Proves the fixtures wire up correctly — tmp_db creates a live SQLite
file the code can write to, tmp_config_dir redirects the Settings
paths, flask_client boots create_app() pointing at tmp_db, and
fake_git_repo yields a git-initialised workspace.

A green run here is the floor for every downstream test that adopts
these fixtures.
"""

from __future__ import annotations

from sqlmodel import select


# ── tmp_db ────────────────────────────────────────────────────

def test_tmp_db_isolates_sqlite_file(tmp_db):
    """DB_PATH is redirected and the schema.sql tables exist."""
    import lib.orm.engine as db_module
    assert str(tmp_db) == db_module.DB_PATH
    assert tmp_db.exists()

    from lib.orm import SessionLocal
    from lib.orm.models import Repo
    with SessionLocal() as session:
        # Fresh DB: no repos.
        assert session.exec(select(Repo)).all() == []


def test_tmp_db_accepts_writes(tmp_db):
    """A round-trip insert + select works against the fresh DB."""
    from lib.orm import SessionLocal
    from lib.orm.models import Repo
    with SessionLocal() as session:
        session.add(Repo(name="demo", path="/tmp/demo", default_branch="main"))
        session.commit()

    with SessionLocal() as session:
        rows = session.exec(select(Repo)).all()
        assert [r.name for r in rows] == ["demo"]


# ── tmp_config_dir ────────────────────────────────────────────

def test_tmp_config_dir_redirects_paths(tmp_config_dir):
    """Settings resolve patterns_dir under the tmp root."""
    from lib.settings import settings
    assert str(settings.data_dir) == str(tmp_config_dir)
    assert str(settings.patterns_dir).startswith(str(tmp_config_dir))


# ── flask_client ──────────────────────────────────────────────

def test_flask_client_boots_against_tmp_db(flask_client):
    """The Flask test client responds to a GET and doesn't touch the
    real DB. /api/status on an empty DB returns an empty list."""
    r = flask_client.get("/api/status")
    assert r.status_code == 200
    assert r.get_json() == []


# ── fake_git_repo ─────────────────────────────────────────────

def test_fake_git_repo_creates_initial_commit(fake_git_repo):
    """The fixture leaves the repo with one commit on the main branch."""
    from lib.sync.git_ops import get_branches
    branches = get_branches(str(fake_git_repo))
    assert "main" in branches
