"""Integration tests for the skill-scope gate in rule_check.handle.

A rule is filtered out unless its `guide` (pattern slug) is deployed
either globally or to the registered repo containing the edited file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List
from unittest import mock

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import rule_check
from lib.rule_engines.base import Rule, Violation


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


class _FakeEngine:
    """Minimal non-grit engine: always-applies rules with the guides we want."""

    kind = "fake"
    language_ids = ("python",)
    project_root = None

    def __init__(self, engine_id: str, rules: List[Rule]):
        self.id = engine_id
        self._rules = rules

    def parse_rules(self):
        return list(self._rules)

    def applies_to(self, rule, file_path, content):
        return True

    def applicable_rules(self, file_path, content):
        from lib.rule_engines.base import default_applicable_rules
        return default_applicable_rules(self, file_path, content)

    def run(self, rule, file_path, repo_root):
        return Violation(rule_id=rule.id, file_path=file_path, match_count=0)


def _make_rule(rule_id: str, guide: str | None) -> Rule:
    return Rule(
        id=rule_id, engine="fake", summary=f"rule {rule_id}",
        severity="warn", triggers=("*.py",), source_file=f"{rule_id}.fake",
        metadata={"guide": guide} if guide else {},
    )


def _payload(file_path: str) -> HookPayload:
    return HookPayload(
        event="PostToolUse",
        tool_name="Edit",
        tool_response={"filePath": file_path},
        tool_input={"file_path": file_path},
        session_id="test-session",
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    from lib.patterns import pattern_scope as ps
    ps.reset_cache()
    yield
    ps.reset_cache()


@pytest.fixture
def fake_engine_only(monkeypatch):
    """Override rule_engines.all_engines to expose just our fake engine."""
    rules = [
        _make_rule("R_GLOBAL",     guide="global-skill"),
        _make_rule("R_REPO_A",     guide="repo-a-skill"),
        _make_rule("R_UNDEPLOYED", guide="undeployed-skill"),
        _make_rule("R_NO_GUIDE",   guide=None),
    ]
    engine = _FakeEngine("fake", rules)

    from lib import rule_engines as re_pkg
    monkeypatch.setattr(re_pkg, "all_engines", lambda: [engine])
    monkeypatch.setattr(
        "lib.rules.engine_rule_disable.disabled_ids", lambda _eid: set()
    )
    return engine


def test_global_skill_fires_outside_any_repo(tmp_db, tmp_path, fake_engine_only):
    _seed_deployment(tmp_db, "global-skill", "global", None)

    f = tmp_path / "outside.py"
    f.write_text("x = 1\n")

    captured: dict = {}

    def _capture(*args, **kwargs):
        captured.update(kwargs)

    with mock.patch.object(rule_check, "_emit_rule_check_span", _capture):
        resp = rule_check.handle(_payload(str(f)))

    assert resp is not None
    applicable_ids = {r["id"] for r in captured["applicable_rules"]}
    # R_GLOBAL (global deployment) + R_NO_GUIDE (no guide → allowed)
    assert applicable_ids == {"R_GLOBAL", "R_NO_GUIDE"}
    skipped_ids = {row["rule_id"] for row in captured["skipped_by_scope"]}
    assert skipped_ids == {"R_REPO_A", "R_UNDEPLOYED"}


def test_repo_skill_fires_inside_its_repo(tmp_db, tmp_path, fake_engine_only):
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    pid = _seed_repo(tmp_db, "repo_a", str(repo_a))
    _seed_deployment(tmp_db, "repo-a-skill", "project", pid)
    _seed_deployment(tmp_db, "global-skill", "global", None)

    inside = repo_a / "code.py"
    inside.write_text("x = 1\n")

    captured: dict = {}

    def _capture(*args, **kwargs):
        captured.update(kwargs)

    with mock.patch.object(rule_check, "_emit_rule_check_span", _capture):
        resp = rule_check.handle(_payload(str(inside)))

    assert resp is not None
    # Three rules pass scope: R_GLOBAL, R_REPO_A, R_NO_GUIDE
    applicable_ids = {r["id"] for r in captured["applicable_rules"]}
    assert "R_GLOBAL" in applicable_ids
    assert "R_REPO_A" in applicable_ids
    assert "R_NO_GUIDE" in applicable_ids
    assert "R_UNDEPLOYED" not in applicable_ids

    skipped_ids = {row["rule_id"] for row in captured["skipped_by_scope"]}
    assert "R_UNDEPLOYED" in skipped_ids
    assert len(captured["skipped_by_scope"]) >= 1


def test_repo_skill_does_not_fire_outside_its_repo(tmp_db, tmp_path, fake_engine_only):
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    pid = _seed_repo(tmp_db, "repo_a", str(repo_a))
    _seed_deployment(tmp_db, "repo-a-skill", "project", pid)

    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n")

    captured: dict = {}

    def _capture(*args, **kwargs):
        captured.update(kwargs)

    with mock.patch.object(rule_check, "_emit_rule_check_span", _capture):
        resp = rule_check.handle(_payload(str(outside)))

    assert resp is not None
    applicable_ids = {r["id"] for r in captured["applicable_rules"]}
    # repo-a-skill is project-scoped to repo_a but file is outside → skipped
    assert "R_REPO_A" not in applicable_ids
    skipped_ids = {row["rule_id"] for row in captured["skipped_by_scope"]}
    assert "R_REPO_A" in skipped_ids
    assert "R_UNDEPLOYED" in skipped_ids


def test_undeployed_skill_never_fires(tmp_db, tmp_path, fake_engine_only):
    # No deployments at all.
    f = tmp_path / "any.py"
    f.write_text("x = 1\n")

    captured: dict = {}

    def _capture(*args, **kwargs):
        captured.update(kwargs)

    with mock.patch.object(rule_check, "_emit_rule_check_span", _capture):
        resp = rule_check.handle(_payload(str(f)))

    assert resp is not None
    applicable_ids = {r["id"] for r in captured["applicable_rules"]}
    # Only R_NO_GUIDE survives (no guide → treated as allowed).
    assert applicable_ids == {"R_NO_GUIDE"}
    skipped_ids = {row["rule_id"] for row in captured["skipped_by_scope"]}
    assert skipped_ids == {"R_GLOBAL", "R_REPO_A", "R_UNDEPLOYED"}


def test_rule_without_guide_keeps_global_behavior(tmp_db, tmp_path, fake_engine_only):
    """A rule with no `guide` metadata is treated as allowed everywhere."""
    f = tmp_path / "any.py"
    f.write_text("x = 1\n")

    captured: dict = {}

    def _capture(*args, **kwargs):
        captured.update(kwargs)

    with mock.patch.object(rule_check, "_emit_rule_check_span", _capture):
        rule_check.handle(_payload(str(f)))

    applicable_ids = {r["id"] for r in captured["applicable_rules"]}
    assert "R_NO_GUIDE" in applicable_ids
