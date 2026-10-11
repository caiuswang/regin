"""`regin users init-db` touches only the auth engine's tables."""

from __future__ import annotations

import sqlite3

import pytest
import typer

from cli.commands import users as users_cmd
from lib.orm import engine
from lib.settings import settings


def _tables(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_standalone_init_db_on_an_initialised_db_changes_nothing(
        tmp_db, monkeypatch, capsys):
    monkeypatch.setattr(settings, "mode", "standalone")
    before = _tables(tmp_db)
    users_cmd.cmd_users_init_db()
    assert _tables(tmp_db) == before
    assert "present" in capsys.readouterr().out


def test_standalone_init_db_does_not_build_the_primary_db(
        tmp_db, monkeypatch, capsys):
    monkeypatch.setattr(settings, "mode", "standalone")
    engine.dispose_engine()
    tmp_db.unlink()
    with pytest.raises(typer.Exit) as exc:
        users_cmd.cmd_users_init_db()
    assert exc.value.exit_code == 1
    assert "regin init" in capsys.readouterr().out
    assert not tmp_db.exists()


def test_shared_init_db_creates_the_mysql_auth_tables(monkeypatch):
    from lib import mysql_db
    calls = []
    monkeypatch.setattr(settings, "mode", "shared")
    monkeypatch.setattr(mysql_db, "init_mysql", lambda: calls.append(1))
    users_cmd.cmd_users_init_db()
    assert calls == [1]
