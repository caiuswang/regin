"""Unit tests for lib.patterns.pattern_scope.

Exercises the three public functions — pattern_allowed_for_file,
pattern_allowed_for_repo, describe — against an isolated `tmp_db`
seeded with PatternDeployment rows.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from lib.patterns.pattern_scope import (
    describe,
    pattern_allowed_for_file,
    pattern_allowed_for_repo,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


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
                     project_id: int | None, deployed_path: str = "/x") -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO pattern_deployments "
            "(pattern_slug, scope, project_id, deployed_path) "
            "VALUES (?, ?, ?, ?)",
            (slug, scope, project_id, deployed_path),
        )
        conn.commit()
    finally:
        conn.close()


# ── pattern_allowed_for_file ─────────────────────────────────

def test_global_skill_fires_anywhere(tmp_db, tmp_path):
    _seed_deployment(tmp_db, "global-skill", "global", None)
    f = tmp_path / "any.java"
    f.write_text("x")
    assert pattern_allowed_for_file("global-skill", str(f)) is True


def test_undeployed_skill_does_not_fire(tmp_db, tmp_path):
    f = tmp_path / "any.java"
    f.write_text("x")
    assert pattern_allowed_for_file("undeployed-skill", str(f)) is False


def test_project_skill_fires_inside_repo(tmp_db, tmp_path):
    repo_path = tmp_path / "alpha"
    repo_path.mkdir()
    pid = _seed_repo(tmp_db, "alpha", str(repo_path))
    _seed_deployment(tmp_db, "repo-skill", "project", pid)

    inside = repo_path / "sub" / "File.java"
    inside.parent.mkdir()
    inside.write_text("x")
    assert pattern_allowed_for_file("repo-skill", str(inside)) is True


def test_project_skill_does_not_fire_outside_repo(tmp_db, tmp_path):
    repo_path = tmp_path / "alpha"
    repo_path.mkdir()
    pid = _seed_repo(tmp_db, "alpha", str(repo_path))
    _seed_deployment(tmp_db, "repo-skill", "project", pid)

    outside = tmp_path / "other.java"
    outside.write_text("x")
    assert pattern_allowed_for_file("repo-skill", str(outside)) is False


def test_skill_with_multiple_project_deployments(tmp_db, tmp_path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    pid_a = _seed_repo(tmp_db, "a", str(a))
    pid_b = _seed_repo(tmp_db, "b", str(b))
    _seed_deployment(tmp_db, "multi", "project", pid_a)
    _seed_deployment(tmp_db, "multi", "project", pid_b)

    fa = a / "file.java"; fa.write_text("x")
    fb = b / "file.java"; fb.write_text("x")
    fc = tmp_path / "file.java"; fc.write_text("x")
    assert pattern_allowed_for_file("multi", str(fa)) is True
    assert pattern_allowed_for_file("multi", str(fb)) is True
    assert pattern_allowed_for_file("multi", str(fc)) is False


def test_none_slug_is_allowed(tmp_db, tmp_path):
    """A rule without a linked guide keeps pre-refactor behavior."""
    f = tmp_path / "any.java"
    f.write_text("x")
    assert pattern_allowed_for_file(None, str(f)) is True
    assert pattern_allowed_for_file("", str(f)) is True


# ── pattern_allowed_for_repo ─────────────────────────────────

def test_repo_match_global_skill(tmp_db):
    _seed_deployment(tmp_db, "g", "global", None)
    assert pattern_allowed_for_repo("g", None) is True
    assert pattern_allowed_for_repo("g", "anything") is True


def test_repo_match_project_skill(tmp_db, tmp_path):
    repo = tmp_path / "alpha"; repo.mkdir()
    pid = _seed_repo(tmp_db, "alpha", str(repo))
    _seed_deployment(tmp_db, "s", "project", pid)
    assert pattern_allowed_for_repo("s", "alpha") is True
    assert pattern_allowed_for_repo("s", "other") is False
    assert pattern_allowed_for_repo("s", None) is False


def test_repo_match_undeployed_skill(tmp_db):
    assert pattern_allowed_for_repo("none", "anything") is False
    assert pattern_allowed_for_repo("none", None) is False


# ── describe ────────────────────────────────────────────────

def test_describe_global(tmp_db):
    _seed_deployment(tmp_db, "g", "global", None)
    out = describe("g")
    assert out == {"global": True, "project_ids": []}


def test_describe_project_only(tmp_db, tmp_path):
    repo = tmp_path / "alpha"; repo.mkdir()
    pid = _seed_repo(tmp_db, "alpha", str(repo))
    _seed_deployment(tmp_db, "s", "project", pid)
    out = describe("s")
    assert out == {"global": False, "project_ids": [pid]}


def test_describe_undeployed(tmp_db):
    out = describe("missing")
    assert out == {"global": False, "project_ids": []}


def test_describe_none_slug(tmp_db):
    assert describe(None) == {"global": True, "project_ids": []}
