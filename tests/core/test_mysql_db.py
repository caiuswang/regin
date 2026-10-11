"""Unit tests for lib.mysql_db config helpers.

Exercises the URL parser, config resolution off `settings.database_url`,
and is_configured. The actual MySQL connection path
(`get_mysql_connection`, `init_mysql`) depends on a live server and
stays out of scope for the unit test.
"""

from __future__ import annotations

import pytest

from lib import mysql_db
from lib.mysql_db import _get_config, _parse_database_url, is_configured
from lib.settings import settings


# ── _parse_database_url ──────────────────────────────────────

def test_parse_url_full():
    cfg = _parse_database_url("mysql://root:secret@db.example:3307/regin")
    assert cfg == {
        "host": "db.example", "port": 3307, "user": "root",
        "password": "secret", "database": "regin",
    }


def test_parse_url_no_password():
    cfg = _parse_database_url("mysql://root@localhost:3306/app")
    assert cfg["user"] == "root"
    assert cfg["password"] == ""


def test_parse_url_default_port():
    cfg = _parse_database_url("mysql://u:p@host/db")
    assert cfg["port"] == 3306


def test_parse_url_default_database():
    cfg = _parse_database_url("mysql://u:p@host:3306")
    assert cfg["database"] == "regin"


def test_parse_url_accepts_pymysql_driver_prefix():
    assert _parse_database_url("mysql+pymysql://u:p@h:3310/d") == {
        "host": "h", "port": 3310, "user": "u",
        "password": "p", "database": "d",
    }


def test_parse_url_rejects_a_non_mysql_scheme():
    with pytest.raises(ValueError):
        _parse_database_url("postgresql://u:p@h/d")


# ── _get_config ──────────────────────────────────────────────

def test_get_config_reads_settings_database_url(monkeypatch):
    monkeypatch.setattr(settings, "database_url",
                        "mysql+pymysql://alice:pw@host:3307/mydb")
    cfg = _get_config()
    assert cfg["user"] == "alice"
    assert cfg["database"] == "mydb"


def test_get_config_without_a_url_raises(monkeypatch):
    monkeypatch.setattr(settings, "database_url", None)
    with pytest.raises(RuntimeError):
        _get_config()


# ── is_configured ────────────────────────────────────────────

def test_is_configured_true_with_database_url(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "mysql://x:x@x/x")
    assert is_configured() is True


def test_is_configured_false_when_nothing_set(monkeypatch):
    monkeypatch.setattr(settings, "database_url", None)
    assert is_configured() is False


# ── get_mysql_connection + init_mysql (pymysql mocked) ───────

def test_get_mysql_connection_passes_config_to_pymysql(
        tmp_path, monkeypatch):
    """get_mysql_connection forwards _get_config() kwargs to pymysql."""
    from unittest.mock import MagicMock
    import pymysql

    monkeypatch.setattr(settings, "database_url", "mysql://u:p@h:3310/db")

    fake_conn = MagicMock()
    called = {}

    def fake_connect(**kwargs):
        called.update(kwargs)
        return fake_conn

    monkeypatch.setattr(pymysql, "connect", fake_connect)
    conn = mysql_db.get_mysql_connection()
    assert conn is fake_conn
    assert called["host"] == "h"
    assert called["port"] == 3310
    assert called["user"] == "u"
    assert called["database"] == "db"
    assert called["autocommit"] is False
    assert called["charset"] == "utf8mb4"


def test_init_mysql_runs_create_table_statements(
        tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    import pymysql

    executed: list[str] = []

    class FakeCursor:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def execute(self, sql):
            executed.append(sql)

    class FakeConn:
        def __init__(self):
            self.committed = False
            self.closed = False
        def cursor(self):
            return FakeCursor()
        def commit(self):
            self.committed = True
        def close(self):
            self.closed = True

    fake = FakeConn()
    monkeypatch.setattr(mysql_db, "get_mysql_connection", lambda: fake)

    mysql_db.init_mysql()
    # Both CREATE TABLE statements ran.
    assert any("CREATE TABLE IF NOT EXISTS users" in s for s in executed)
    assert any("CREATE TABLE IF NOT EXISTS audit_log" in s for s in executed)
    # Connection committed + closed even on success path.
    assert fake.committed is True
    assert fake.closed is True


def test_init_mysql_closes_connection_on_exception(monkeypatch):
    class BoomCursor:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def execute(self, sql):
            raise RuntimeError("boom")

    class FakeConn:
        def __init__(self):
            self.closed = False
        def cursor(self):
            return BoomCursor()
        def commit(self):
            pass
        def close(self):
            self.closed = True

    fake = FakeConn()
    monkeypatch.setattr(mysql_db, "get_mysql_connection", lambda: fake)

    import pytest
    with pytest.raises(RuntimeError):
        mysql_db.init_mysql()
    assert fake.closed is True
