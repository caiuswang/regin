"""Unit tests for cli.commands.db."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cli.commands import db as db_cmd
from lib import hooks_wiring
from lib.providers.claude import ClaudeProvider
from lib.settings import settings

_OURS_POST = hooks_wiring.interpreter_prefix() + "-P -m hook_manager PostToolUse --agent-type claude"
_OTHER_POST = "/other/regin/.venv/bin/python -P -m hook_manager PostToolUse --agent-type claude"


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
                    {"type": "command", "command": _OURS_POST},
                    {"type": "command", "command": _OTHER_POST},
                    {"type": "command", "command": "python /keep/me.py"},
                ]},
            ],
        },
    }))

    # `db_cmd` no longer holds its own `DB_PATH`; it resolves
    # `lib.orm.engine.DB_PATH` at call time, which the autouse `tmp_db`
    # fixture already points at this tmp file. Patching a module-level copy
    # here was what let `cmd_init --force` delete the real DB whenever a
    # caller forgot it.
    monkeypatch.setattr(settings, "patterns_dir", str(patterns_dir))
    monkeypatch.setattr(db_cmd, "HOOK_MANAGER_CONFIG_PATH", str(hook_manager_cfg))
    monkeypatch.setattr(db_cmd, "CLAUDE_SETTINGS_PATH", str(claude_settings))
    monkeypatch.setattr(db_cmd, "_PROVIDER", ClaudeProvider({"hook_settings_path": claude_settings}))
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
    assert _OURS_POST not in commands
    assert _OTHER_POST in commands
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


_KEEP = {"type": "command", "command": "python /keep/me.py"}
_HM = {"type": "command",
       "command": hooks_wiring.interpreter_prefix() + "-P -m hook_manager Stop --agent-type claude"}


@pytest.mark.parametrize("before,removed,after", [
    ({"hooks": {
        "Stop": [{"hooks": [_HM]}],
        "PostToolUse": [{"matcher": "x", "hooks": [_HM, _KEEP]},
                        {"hooks": "not-a-list"}],
        "Weird": "not-a-list",
    }, "other": 1}, 2,
     {"hooks": {
         "PostToolUse": [{"matcher": "x", "hooks": [_KEEP]}, {"hooks": "not-a-list"}],
         "Weird": "not-a-list",
     }, "other": 1}),
    ({"hooks": {"Stop": [{"hooks": [_HM]}]}, "other": 1}, 1, {"other": 1}),
    ({"hooks": {"Stop": [{"hooks": [_KEEP]}]}}, 0, None),
    ({"hooks": ["x"]}, 0, None),
    ({"other": 1}, 0, None),
])
def test_clear_hook_manager_settings_shapes(tmp_path, monkeypatch, before, removed, after):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(before))
    monkeypatch.setattr(db_cmd, "CLAUDE_SETTINGS_PATH", str(path))
    monkeypatch.setattr(db_cmd, "_PROVIDER", ClaudeProvider({"hook_settings_path": path}))
    assert db_cmd._clear_hook_manager_settings() == removed
    assert json.loads(path.read_text()) == (before if after is None else after)


def test_clear_hook_manager_settings_missing_or_corrupt(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(db_cmd, "CLAUDE_SETTINGS_PATH", str(path))
    monkeypatch.setattr(db_cmd, "_PROVIDER", ClaudeProvider({"hook_settings_path": path}))
    assert db_cmd._clear_hook_manager_settings() == 0
    path.write_text("{not json")
    assert db_cmd._clear_hook_manager_settings() == 0


@pytest.fixture
def init_stubs(monkeypatch, tmp_path):
    from lib import db_migrate, prompt_templates
    calls: list[str] = []
    monkeypatch.setattr(db_cmd, "_init_db", lambda: calls.append("init_db"))
    monkeypatch.setattr(db_migrate, "stamp_head", lambda: calls.append("stamp"))
    monkeypatch.setattr(prompt_templates, "seed_builtin_skeletons",
                        lambda: calls.append("seed"))
    monkeypatch.setattr(settings, "patterns_dir", str(tmp_path / "patterns"))
    monkeypatch.setattr(db_cmd._engine, "DB_PATH", "/x/regin.db")
    return calls


@pytest.mark.parametrize("state,expected", [
    ((1, True, True, 1), [
        "Cleared existing local state before initialization.",
        "Removed 1 deployed skill directory.",
        "Removed JWT signing secret; existing login tokens are now invalid.",
        "Removed hook-manager toggle state.",
        "Removed 1 hook_manager routing entry from Claude settings.",
    ]),
    ((3, False, False, 2), [
        "Cleared existing local state before initialization.",
        "Removed 3 deployed skill directories.",
        "Removed 2 hook_manager routing entries from Claude settings.",
    ]),
    ((0, False, False, 0), [
        "Cleared existing local state before initialization.",
    ]),
])
def test_cmd_init_force_messages(init_stubs, monkeypatch, capsys, state, expected):
    monkeypatch.setattr(db_cmd, "db_exists", lambda: True)
    monkeypatch.setattr(db_cmd, "_force_reset_state", lambda: state)
    db_cmd.cmd_init(force=True)
    lines = capsys.readouterr().out.splitlines()
    assert lines[:-2] == expected
    assert lines[-2] == "Initialized database at /x/regin.db"
    assert init_stubs == ["init_db", "stamp", "seed"]


def test_cmd_init_existing_db_without_force(init_stubs, monkeypatch, capsys):
    monkeypatch.setattr(db_cmd, "db_exists", lambda: True)
    db_cmd.cmd_init(force=False)
    assert capsys.readouterr().out.splitlines() == [
        "Database already exists at /x/regin.db", "Use --force to reinitialize."]
    assert init_stubs == []


def test_cmd_init_fresh_without_force(init_stubs, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(db_cmd, "db_exists", lambda: False)
    monkeypatch.setattr(db_cmd, "_force_reset_state",
                        lambda: pytest.fail("must not reset without --force"))
    db_cmd.cmd_init(force=False)
    assert capsys.readouterr().out.splitlines() == [
        "Initialized database at /x/regin.db",
        f"Pattern directory ready at {tmp_path / 'patterns'}"]
    assert (tmp_path / "patterns" / "_index").is_dir()
