"""Unit tests for cli.commands.db."""

from __future__ import annotations

import json
import sqlite3

from cli.commands import db as db_cmd
from lib.settings import settings


def test_cmd_init_force_clears_db_rows_deployments_and_jwt_secret(
        tmp_db, tmp_path, monkeypatch):
    from lib import auth as auth_mod

    patterns_dir = tmp_path / "patterns"
    repo_dir = tmp_path / "example-repo"
    deployed_dir = repo_dir / ".claude" / "skills" / "demo-skill"
    deployed_dir.mkdir(parents=True)
    (deployed_dir / "SKILL.md").write_text("stub")

    jwt_secret = tmp_path / "jwt_secret.txt"
    jwt_secret.write_text("secret")
    hook_manager_cfg = tmp_path / "hook-manager-config.json"
    hook_manager_cfg.write_text('{"disabled_handlers":["rule_check"]}')
    claude_settings = tmp_path / "settings.json"
    claude_settings.write_text(json.dumps({
        "hooks": {
            "PostToolUse": [
                {"hooks": [
                    {"type": "command", "command": "python -m hook_manager PostToolUse"},
                    {"type": "command", "command": "python /keep/me.py"},
                ]},
            ],
        },
    }))

    monkeypatch.setattr(db_cmd, "DB_PATH", str(tmp_db))
    monkeypatch.setattr(settings, "patterns_dir", str(patterns_dir))
    monkeypatch.setattr(db_cmd, "HOOK_MANAGER_CONFIG_PATH", str(hook_manager_cfg))
    monkeypatch.setattr(db_cmd, "CLAUDE_SETTINGS_PATH", str(claude_settings))
    monkeypatch.setattr(auth_mod, "_SECRET_PATH", str(jwt_secret))

    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO tags (name, category) VALUES (?, ?)",
            ("stale-tag", "concept"),
        )
        conn.execute(
            "INSERT INTO experiments (pattern_slug, name, conceal_spec) "
            "VALUES (?, ?, ?)",
            ("demo", "exp", '{"sections": []}'),
        )
        conn.execute(
            "INSERT INTO users (username, display_name, password_hash, role) "
            "VALUES (?, ?, ?, ?)",
            ("alice", "Alice", "salt:hash", "admin"),
        )
        conn.execute(
            "INSERT INTO pattern_deployments "
            "(pattern_slug, scope, project_id, deployed_path) "
            "VALUES (?, ?, ?, ?)",
            ("demo-skill", "project", None, str(deployed_dir)),
        )
        conn.commit()
    finally:
        conn.close()

    db_cmd.cmd_init(force=True)

    assert not deployed_dir.exists()
    assert not jwt_secret.exists()
    assert not hook_manager_cfg.exists()
    assert (patterns_dir / "_index").is_dir()
    settings_after = json.loads(claude_settings.read_text())
    commands = [
        hook["command"]
        for entry in settings_after["hooks"]["PostToolUse"]
        for hook in entry["hooks"]
    ]
    assert "python -m hook_manager PostToolUse" not in commands
    assert "python /keep/me.py" in commands

    conn = sqlite3.connect(str(tmp_db))
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM tags WHERE name = 'stale-tag'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM experiments WHERE pattern_slug = 'demo'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM users WHERE username = 'alice'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM pattern_deployments "
            "WHERE pattern_slug = 'demo-skill'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_reset_shared_auth_tables_drops_and_recreates(monkeypatch):
    from lib import mysql_db

    executed: list[str] = []
    committed = False
    closed = False
    init_called = False

    class _Cursor:
        def execute(self, sql):
            executed.append(sql)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Conn:
        def cursor(self):
            return _Cursor()

        def commit(self):
            nonlocal committed
            committed = True

        def close(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr(mysql_db, "get_mysql_connection", lambda: _Conn())

    def _fake_init_mysql():
        nonlocal init_called
        init_called = True

    monkeypatch.setattr(mysql_db, "init_mysql", _fake_init_mysql)

    db_cmd._reset_shared_auth_tables()

    assert executed == [
        "DROP TABLE IF EXISTS audit_log",
        "DROP TABLE IF EXISTS users",
    ]
    assert committed is True
    assert closed is True
    assert init_called is True
