"""Unit tests for topic ignore-rule matching."""

from __future__ import annotations

from lib.topics.ignores import IgnoreMatcher, is_ignored, load_ignore_rules


def test_load_ignore_rules_applies_defaults(tmp_path):
    matcher = load_ignore_rules(tmp_path)

    assert matcher.is_ignored(".git/config")
    assert matcher.is_ignored(tmp_path / ".git" / "config")
    assert matcher.is_ignored("frontend/node_modules/vue/index.js")
    assert matcher.is_ignored("build/app.js")
    assert matcher.is_ignored("src/__pycache__/mod.cpython-312.pyc")
    assert not matcher.is_ignored("src/app.py")


def test_load_ignore_rules_reads_gitignore_and_reginignore(tmp_path):
    (tmp_path / ".gitignore").write_text("""
# comments and blank lines are ignored

*.log
logs/
""")
    (tmp_path / ".reginignore").write_text("""
tmp-topic.json
""")

    matcher = load_ignore_rules(tmp_path)

    assert matcher.is_ignored("debug.log")
    assert matcher.is_ignored("src/debug.log")
    assert matcher.is_ignored("logs/today.txt")
    assert matcher.is_ignored("nested/logs/today.txt")
    assert matcher.is_ignored("tmp-topic.json")
    assert not matcher.is_ignored("src/app.py")


def test_rooted_directory_pattern_only_matches_repo_root(tmp_path):
    (tmp_path / ".gitignore").write_text("""
/root-only/
cache/
""")

    matcher = load_ignore_rules(tmp_path)

    assert matcher.is_ignored("root-only/app.js")
    assert not matcher.is_ignored("pkg/root-only/app.js")
    assert matcher.is_ignored("pkg/cache/app.js")


def test_negation_reincludes_later_paths(tmp_path):
    (tmp_path / ".gitignore").write_text("""
*.log
logs/
!keep.log
!logs/keep.log
""")

    matcher = load_ignore_rules(tmp_path)

    assert matcher.is_ignored("error.log")
    assert not matcher.is_ignored("keep.log")
    assert matcher.is_ignored("logs/error.txt")
    assert not matcher.is_ignored("logs/keep.log")


def test_later_ignore_can_override_earlier_negation(tmp_path):
    (tmp_path / ".gitignore").write_text("""
*.log
!keep.log
keep.log
""")

    matcher = load_ignore_rules(tmp_path)

    assert matcher.is_ignored("keep.log")


def test_is_ignored_accepts_matcher_or_rules(tmp_path):
    matcher = load_ignore_rules(tmp_path)

    assert is_ignored("dist/app.js", matcher)
    assert is_ignored("dist/app.js", matcher.rules)
    assert isinstance(matcher, IgnoreMatcher)
