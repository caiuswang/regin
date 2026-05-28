"""Unit tests for lib.rule_engines.repo_scope.repo_for_path."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from lib.rule_engines.repo_scope import repo_for_path


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


def test_returns_none_when_no_repos(tmp_db, tmp_path):
    assert repo_for_path(str(tmp_path / "x.java")) is None


def test_matches_single_repo(tmp_db, tmp_path):
    repo_root = tmp_path / "alpha"
    repo_root.mkdir()
    _seed_repo(tmp_db, "alpha", str(repo_root))
    f = repo_root / "src" / "A.java"
    f.parent.mkdir()
    f.write_text("x")
    got = repo_for_path(str(f))
    assert got is not None
    assert got.name == "alpha"


def test_longest_prefix_wins(tmp_db, tmp_path):
    outer = tmp_path / "outer"
    inner = outer / "sub"
    inner.mkdir(parents=True)
    _seed_repo(tmp_db, "outer", str(outer))
    _seed_repo(tmp_db, "inner", str(inner))

    f = inner / "deep" / "File.java"
    f.parent.mkdir()
    f.write_text("x")
    got = repo_for_path(str(f))
    assert got is not None
    assert got.name == "inner"


def test_path_outside_returns_none(tmp_db, tmp_path):
    repo_root = tmp_path / "alpha"
    repo_root.mkdir()
    _seed_repo(tmp_db, "alpha", str(repo_root))
    f = tmp_path / "sibling.java"
    f.write_text("x")
    assert repo_for_path(str(f)) is None


def test_prefix_does_not_match_unrelated_name(tmp_db, tmp_path):
    """Repo at /a/b must not match a file at /a/bb/x."""
    repo_root = tmp_path / "alpha"
    repo_root.mkdir()
    _seed_repo(tmp_db, "alpha", str(repo_root))

    sibling = tmp_path / "alphabet"
    sibling.mkdir()
    f = sibling / "x.java"
    f.write_text("x")
    assert repo_for_path(str(f)) is None
