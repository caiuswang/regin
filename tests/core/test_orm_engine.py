"""Unit tests for lib.orm.engine.

Verifies the SQLAlchemy engine + SessionLocal factory wiring added in
Phase B.1, plus the url-change-triggered rebuild added in Phase B.4 so
test monkey-patches of `lib.orm.engine.DB_PATH` pick up on the next
SessionLocal().
"""

from __future__ import annotations

from sqlmodel import select

from lib.orm import engine as engine_module
from lib.orm.engine import (
    SessionLocal, dispose_engine, get_auth_engine, get_engine,
)
from lib.orm.models import Repo


# ── Primary engine lifecycle ─────────────────────────────────

def test_get_engine_lazy_caches(tmp_db):
    first = get_engine()
    second = get_engine()
    assert first is second


def test_dispose_engine_clears_cache(tmp_db):
    first = get_engine()
    dispose_engine()
    second = get_engine()
    assert first is not second


def test_engine_rebuilds_when_db_path_changes(tmp_db, tmp_path, monkeypatch):
    """tmp_db already redirected lib.orm.engine.DB_PATH once before SessionLocal
    opened; flipping the path mid-test should trigger rebuild on the
    next get_engine call."""
    first = get_engine()
    first_url = str(first.url)

    import lib.orm.engine as db_module
    alt_path = tmp_path / "alt.db"
    alt_path.touch()  # file just needs to exist
    monkeypatch.setattr(db_module, "DB_PATH", str(alt_path))

    second = get_engine()
    assert second is not first
    assert str(second.url) != first_url
    assert str(alt_path) in str(second.url)


# ── SessionLocal returns sqlmodel Session ────────────────────

def test_session_local_returns_sqlmodel_session(tmp_db):
    with SessionLocal() as session:
        # SQLModel's Session subclass exposes `.exec()` on top of
        # SQLAlchemy's `.execute()`. If it's the right class,
        # a select(...) round-trip works.
        rows = session.exec(select(Repo)).all()
        assert rows == []


def test_session_local_commits_are_visible_across_sessions(tmp_db):
    with SessionLocal() as s:
        s.add(Repo(name="test-repo", path="/tmp/x", default_branch="main"))
        s.commit()

    with SessionLocal() as s:
        rows = s.exec(select(Repo).where(Repo.name == "test-repo")).all()
        assert len(rows) == 1


# ── Auth engine dispatch ─────────────────────────────────────

def test_auth_engine_aliases_primary_in_standalone_mode(tmp_db):
    # Standalone (default) → auth shares the primary SQLite file.
    primary = get_engine()
    auth = get_auth_engine()
    assert auth is primary


def test_auth_engine_invalidates_with_primary_on_db_path_change(
        tmp_db, tmp_path, monkeypatch):
    """In standalone mode, auth engine tracks the primary engine —
    both should rebuild when DB_PATH changes."""
    first_auth = get_auth_engine()

    import lib.orm.engine as db_module
    alt_path = tmp_path / "alt.db"
    alt_path.touch()
    monkeypatch.setattr(db_module, "DB_PATH", str(alt_path))

    second_auth = get_auth_engine()
    assert second_auth is not first_auth


# ── URL resolution ───────────────────────────────────────────

def test_resolve_primary_url_uses_lib_db_db_path(tmp_db):
    url = engine_module._resolve_primary_url()
    assert url.startswith("sqlite:///")
    assert str(tmp_db) in url


def test_resolve_auth_url_none_in_standalone_mode(monkeypatch):
    """engine.py did `from lib.settings import settings` at module
    load, capturing the singleton by reference. Patch
    `engine_module.settings` (not `lib.settings.settings`) so the
    resolver sees the fresh instance."""
    from lib import settings as settings_module
    fresh = settings_module.Settings(mode="standalone")
    monkeypatch.setattr(engine_module, "settings", fresh)
    assert engine_module._resolve_auth_url() is None


def test_resolve_auth_url_rewrites_mysql_to_pymysql(monkeypatch):
    from lib import settings as settings_module
    fresh = settings_module.Settings(
        mode="shared",
        database_url="mysql://root:root@localhost:3306/regin",
    )
    monkeypatch.setattr(engine_module, "settings", fresh)
    url = engine_module._resolve_auth_url()
    assert url is not None
    assert url.startswith("mysql+pymysql://")
