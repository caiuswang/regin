"""Unit tests for lib.patterns.pattern_deployments.

Uses tmp_db so each test gets an isolated SQLite file. Exercises the
three public functions — record_deployment (with the global-vs-project
null semantics), list_deployments (including the repo + user joins),
and remove_deployment.
"""

from __future__ import annotations

import pytest

from lib.patterns.pattern_deployments import (
    list_deployments, record_deployment, remove_deployment,
)


# ── record_deployment ────────────────────────────────────────

def test_record_deployment_global_creates_row(tmp_db):
    record_deployment(
        pattern_slug="user-account", scope="global",
        project_id=None, deployed_path="/home/x/.claude/skills/api",
        user_id=None,
    )
    rows = list_deployments(pattern_slug="user-account")
    assert len(rows) == 1
    assert rows[0]["scope"] == "global"
    assert rows[0]["deployed_path"] == "/home/x/.claude/skills/api"


def test_record_deployment_replaces_existing_global(tmp_db):
    """SQLite's UNIQUE ignores NULL pairs so the old raw-SQL version
    had to DELETE + INSERT. The SQLModel rewrite keeps the same
    `one global deployment per slug` invariant."""
    record_deployment("slug", "global", None, "/old/path", None)
    record_deployment("slug", "global", None, "/new/path", None)
    rows = list_deployments(pattern_slug="slug")
    assert len(rows) == 1
    assert rows[0]["deployed_path"] == "/new/path"


def test_record_deployment_global_and_project_coexist(tmp_db):
    """A slug can have one global deployment plus one deployment per
    project — (slug, scope, project_id) is the composite uniqueness
    key."""
    import sqlite3
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO repos (name, path, default_branch) "
            "VALUES ('example', '/tmp/example', 'main')"
        )
        conn.commit()
        project_id = conn.execute(
            "SELECT id FROM repos WHERE name = 'example'"
        ).fetchone()[0]
    finally:
        conn.close()

    record_deployment("slug", "global", None, "/global", None)
    record_deployment("slug", "project", project_id, "/per-project", None)

    rows = list_deployments(pattern_slug="slug")
    assert len(rows) == 2
    scopes = {r["scope"] for r in rows}
    assert scopes == {"global", "project"}


# ── remove_deployment ────────────────────────────────────────

def test_remove_deployment_returns_true_when_row_existed(tmp_db):
    record_deployment("slug", "global", None, "/path", None)
    assert remove_deployment("slug", "global", None) is True
    assert list_deployments(pattern_slug="slug") == []


def test_remove_deployment_returns_false_on_miss(tmp_db):
    assert remove_deployment("nonexistent", "global", None) is False


# ── list_deployments joins ───────────────────────────────────

def test_list_deployments_joins_project_name(tmp_db):
    import sqlite3
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO repos (name, path, default_branch) "
            "VALUES ('example-svc', '/tmp/example-svc', 'main')"
        )
        conn.commit()
        pid = conn.execute("SELECT id FROM repos WHERE name = 'example-svc'").fetchone()[0]
    finally:
        conn.close()

    record_deployment("slug", "project", pid, "/repos/example-svc/.claude/skills/slug", None)
    rows = list_deployments(pattern_slug="slug")
    assert len(rows) == 1
    assert rows[0]["project_name"] == "example-svc"
    assert rows[0]["project_path"] == "/tmp/example-svc"


def test_list_deployments_joins_user_username(tmp_db):
    """deployed_by FK → users.id surfaces as deployed_by_username."""
    import sqlite3
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO users (username, display_name, password_hash, role) "
            "VALUES ('tao', 'Tao', 'x:y', 'admin')"
        )
        conn.commit()
        uid = conn.execute("SELECT id FROM users WHERE username = 'tao'").fetchone()[0]
    finally:
        conn.close()

    record_deployment("slug", "global", None, "/path", uid)
    rows = list_deployments(pattern_slug="slug")
    assert rows[0]["deployed_by_username"] == "tao"


def test_list_deployments_filter_by_project(tmp_db):
    import sqlite3
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.executemany(
            "INSERT INTO repos (name, path, default_branch) VALUES (?, ?, ?)",
            [("alpha", "/alpha", "main"), ("beta", "/beta", "main")],
        )
        conn.commit()
        a = conn.execute("SELECT id FROM repos WHERE name = 'alpha'").fetchone()[0]
        b = conn.execute("SELECT id FROM repos WHERE name = 'beta'").fetchone()[0]
    finally:
        conn.close()

    record_deployment("slug", "project", a, "/alpha/.claude/skills/slug", None)
    record_deployment("slug", "project", b, "/beta/.claude/skills/slug", None)

    only_a = list_deployments(pattern_slug="slug", project_id=a)
    assert len(only_a) == 1
    assert only_a[0]["project_name"] == "alpha"
