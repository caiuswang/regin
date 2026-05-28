"""Tests for skill-scope behavior on /api/rules.

The listing returns a per-rule `scope` field derived from
PatternDeployment rows, and `?repo=<name>` filters out rules whose
guide isn't deployed globally or to that repo.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lib.patterns import pattern_scope


@pytest.fixture(autouse=True)
def _reset_cache():
    pattern_scope.reset_cache()
    yield
    pattern_scope.reset_cache()


@pytest.fixture
def fake_grit_rules(monkeypatch):
    """Two grit rules — one guide is global, one is repo-scoped."""
    from web.blueprints import rules as rules_bp
    from lib.rules import grit_rule_index as gri

    rules_data = [
        {
            "id": "global_rule",
            "engine": "grit",
            "layer": "entity",
            "triggers": ["*.java"],
            "severity": "warn",
            "guide": "global-skill",
            "summary": "global",
            "source_file": ".grit/patterns/java/g.grit",
        },
        {
            "id": "scoped_rule",
            "engine": "grit",
            "layer": "entity",
            "triggers": ["*.java"],
            "severity": "warn",
            "guide": "repo-skill",
            "summary": "scoped",
            "source_file": ".grit/patterns/java/s.grit",
        },
        {
            "id": "undeployed_rule",
            "engine": "grit",
            "layer": "entity",
            "triggers": ["*.java"],
            "severity": "warn",
            "guide": "undeployed-skill",
            "summary": "undeployed",
            "source_file": ".grit/patterns/java/u.grit",
        },
    ]
    payload = {"version": 1, "rules": rules_data}
    monkeypatch.setattr(rules_bp, "load_rules_index", lambda: payload)
    monkeypatch.setattr(gri, "load_rules_index", lambda: payload)
    monkeypatch.setattr(
        rules_bp.rule_engines,
        "all_engines",
        lambda: [
            type("GritStub", (), {
                "id": "grit", "kind": "grit",
                "language_ids": ("java",), "grit_dir": "/tmp/.grit",
            })()
        ],
    )
    return rules_data


def _seed_repo(db_path: Path, name: str, path: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO repos (name, path, default_branch) VALUES (?, ?, 'main')",
            (name, path),
        )
        conn.commit()
        return conn.execute(
            "SELECT id FROM repos WHERE name = ?", (name,)
        ).fetchone()[0]
    finally:
        conn.close()


def _seed_deployment(db_path: Path, slug: str, scope: str,
                     project_id: int | None) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO pattern_deployments "
            "(pattern_slug, scope, project_id, deployed_path) "
            "VALUES (?, ?, ?, '/x')",
            (slug, scope, project_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── scope decoration ────────────────────────────────────────

def test_rules_carry_scope_descriptor(flask_client, tmp_db, fake_grit_rules):
    _seed_deployment(tmp_db, "global-skill", "global", None)
    repo_pid = _seed_repo(tmp_db, "alpha", "/tmp/alpha")
    _seed_deployment(tmp_db, "repo-skill", "project", repo_pid)

    body = flask_client.get("/api/rules").get_json()
    by_id = {
        r["id"]: r
        for _g, rules in body["grouped"]
        for r in rules
    }

    assert by_id["global_rule"]["scope"] == {"global": True, "project_ids": []}
    assert by_id["scoped_rule"]["scope"] == {"global": False, "project_ids": [repo_pid]}
    assert by_id["undeployed_rule"]["scope"] == {"global": False, "project_ids": []}


# ── repo filter ─────────────────────────────────────────────

def test_repo_filter_keeps_global_and_matching_repo(flask_client, tmp_db, fake_grit_rules):
    _seed_deployment(tmp_db, "global-skill", "global", None)
    repo_pid = _seed_repo(tmp_db, "alpha", "/tmp/alpha")
    _seed_deployment(tmp_db, "repo-skill", "project", repo_pid)

    body = flask_client.get("/api/rules?repo=alpha").get_json()
    ids = {r["id"] for _g, rules in body["grouped"] for r in rules}

    assert "global_rule" in ids
    assert "scoped_rule" in ids
    assert "undeployed_rule" not in ids
    assert body["repo_filter"] == "alpha"
    assert body["total"] == 2


def test_repo_filter_excludes_other_repos_rules(flask_client, tmp_db, fake_grit_rules):
    repo_pid = _seed_repo(tmp_db, "alpha", "/tmp/alpha")
    _seed_deployment(tmp_db, "repo-skill", "project", repo_pid)

    body = flask_client.get("/api/rules?repo=beta").get_json()
    ids = {r["id"] for _g, rules in body["grouped"] for r in rules}

    assert "scoped_rule" not in ids
    # No matching repo means only globals survive — none here, so empty.
    assert body["total"] == 0


def test_no_repo_filter_returns_all(flask_client, tmp_db, fake_grit_rules):
    body = flask_client.get("/api/rules").get_json()
    assert body["repo_filter"] is None
    assert body["total"] == 3
