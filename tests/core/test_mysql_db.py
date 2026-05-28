"""Unit tests for lib.mysql_db config helpers.

Exercises the URL parser, config resolution (settings / env / key
breakdown), and is_configured. The actual MySQL connection path
(`get_mysql_connection`, `init_mysql`) depends on a live server and
stays out of scope for the unit test.
"""

from __future__ import annotations

import json

from lib import mysql_db
from lib.mysql_db import _get_config, _parse_database_url, is_configured


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


# ── _get_config ──────────────────────────────────────────────

def test_get_config_honours_database_url_from_settings(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "database_url": "mysql://alice:pw@host:3307/mydb",
    }))
    from lib import settings as _config_module
    monkeypatch.setattr(_config_module, "SETTINGS_PATH", str(settings))
    monkeypatch.setattr(_config_module, "SETTINGS_LOCAL_PATH",
                        str(tmp_path / "none.json"))
    monkeypatch.delenv("REGIN_DATABASE_URL", raising=False)
    cfg = _get_config()
    assert cfg["user"] == "alice"
    assert cfg["database"] == "mydb"


def test_get_config_honours_env_var(tmp_path, monkeypatch):
    from lib import settings as _config_module
    monkeypatch.setattr(_config_module, "SETTINGS_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(_config_module, "SETTINGS_LOCAL_PATH", str(tmp_path / "l.json"))
    monkeypatch.setenv("REGIN_DATABASE_URL", "mysql://env:envpw@eh:3308/envdb")
    cfg = _get_config()
    assert cfg["host"] == "eh"
    assert cfg["database"] == "envdb"


def test_get_config_falls_back_to_individual_keys(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "db_host": "myhost", "db_port": 3309,
        "db_user": "u", "db_password": "p", "db_name": "mydb",
    }))
    from lib import settings as _config_module
    monkeypatch.setattr(_config_module, "SETTINGS_PATH", str(settings))
    monkeypatch.setattr(_config_module, "SETTINGS_LOCAL_PATH",
                        str(tmp_path / "l.json"))
    monkeypatch.delenv("REGIN_DATABASE_URL", raising=False)
    cfg = _get_config()
    assert cfg == {
        "host": "myhost", "port": 3309, "user": "u",
        "password": "p", "database": "mydb",
    }


def test_get_config_final_fallback_defaults(tmp_path, monkeypatch):
    from lib import settings as _config_module
    monkeypatch.setattr(_config_module, "SETTINGS_PATH", str(tmp_path / "no.json"))
    monkeypatch.setattr(_config_module, "SETTINGS_LOCAL_PATH",
                        str(tmp_path / "no2.json"))
    monkeypatch.delenv("REGIN_DATABASE_URL", raising=False)
    cfg = _get_config()
    assert cfg == {
        "host": "localhost", "port": 3306, "user": "root",
        "password": "", "database": "regin",
    }


# ── is_configured ────────────────────────────────────────────

def test_is_configured_true_with_database_url(tmp_path, monkeypatch):
    settings = tmp_path / "s.json"
    settings.write_text(json.dumps({"database_url": "mysql://x:x@x/x"}))
    from lib import settings as _config_module
    monkeypatch.setattr(_config_module, "SETTINGS_PATH", str(settings))
    monkeypatch.setattr(_config_module, "SETTINGS_LOCAL_PATH",
                        str(tmp_path / "l.json"))
    monkeypatch.delenv("REGIN_DATABASE_URL", raising=False)
    assert is_configured() is True


def test_is_configured_true_with_env_var(tmp_path, monkeypatch):
    from lib import settings as _config_module
    monkeypatch.setattr(_config_module, "SETTINGS_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(_config_module, "SETTINGS_LOCAL_PATH",
                        str(tmp_path / "l.json"))
    monkeypatch.setenv("REGIN_DATABASE_URL", "mysql://x:x@x/x")
    assert is_configured() is True


def test_is_configured_true_with_db_host(tmp_path, monkeypatch):
    settings = tmp_path / "s.json"
    settings.write_text(json.dumps({"db_host": "foo"}))
    from lib import settings as _config_module
    monkeypatch.setattr(_config_module, "SETTINGS_PATH", str(settings))
    monkeypatch.setattr(_config_module, "SETTINGS_LOCAL_PATH",
                        str(tmp_path / "l.json"))
    monkeypatch.delenv("REGIN_DATABASE_URL", raising=False)
    assert is_configured() is True


def test_is_configured_false_when_nothing_set(tmp_path, monkeypatch):
    from lib import settings as _config_module
    monkeypatch.setattr(_config_module, "SETTINGS_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(_config_module, "SETTINGS_LOCAL_PATH",
                        str(tmp_path / "l.json"))
    monkeypatch.delenv("REGIN_DATABASE_URL", raising=False)
    assert is_configured() is False


# ── get_mysql_connection + init_mysql (pymysql mocked) ───────

def test_get_mysql_connection_passes_config_to_pymysql(
        tmp_path, monkeypatch):
    """get_mysql_connection forwards _get_config() kwargs to pymysql."""
    from unittest.mock import MagicMock
    import pymysql

    # Isolate settings resolution.
    from lib import settings as _config_module
    monkeypatch.setattr(_config_module, "SETTINGS_PATH",
                        str(tmp_path / "s.json"))
    monkeypatch.setattr(_config_module, "SETTINGS_LOCAL_PATH",
                        str(tmp_path / "l.json"))
    monkeypatch.setenv(
        "REGIN_DATABASE_URL", "mysql://u:p@h:3310/db",
    )

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
