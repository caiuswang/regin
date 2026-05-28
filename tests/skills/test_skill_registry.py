"""Unit tests for lib.skills.skill_registry.

Redirects PATTERNS_DIR (and SKILLS_DIR) to tmp paths so the tests
exercise the discovery + lookup logic without depending on the
user's real patterns/ tree.
"""

from __future__ import annotations

import os

import pytest

from lib.skills import skill_registry
from lib.settings import settings


@pytest.fixture
def tmp_skills_setup(tmp_path, monkeypatch):
    """Point PATTERNS_DIR and SKILLS_DIR at tmp dirs."""
    patterns = tmp_path / "patterns"
    skills = tmp_path / "skills"
    patterns.mkdir()
    skills.mkdir()
    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(settings, "skills_dir", str(skills))
    return patterns, skills


def _make_pattern_dir(patterns_root, slug, with_skill_md=True):
    d = patterns_root / slug
    d.mkdir()
    if with_skill_md:
        (d / "SKILL.md").write_text(
            f"---\nprocedure: {slug}\n---\n# {slug}\n"
        )
    return d


# ── discovery ────────────────────────────────────────────────

def test_all_ids_includes_builtin_auto_skill(configured_grit_engine, tmp_skills_setup):
    ids = skill_registry.all_ids()
    assert "grit-rules" in ids


def test_all_ids_discovers_pattern_dirs(tmp_skills_setup):
    patterns, _ = tmp_skills_setup
    _make_pattern_dir(patterns, "my-pattern")
    _make_pattern_dir(patterns, "another")
    ids = skill_registry.all_ids()
    assert "my-pattern" in ids
    assert "another" in ids


def test_all_ids_skips_underscore_and_dot_prefixed(tmp_skills_setup):
    patterns, _ = tmp_skills_setup
    _make_pattern_dir(patterns, "_index")
    _make_pattern_dir(patterns, ".hidden")
    _make_pattern_dir(patterns, "real-one")
    ids = skill_registry.all_ids()
    assert "real-one" in ids
    assert "_index" not in ids
    assert ".hidden" not in ids


def test_all_ids_skips_dirs_without_skill_md(tmp_skills_setup):
    patterns, _ = tmp_skills_setup
    _make_pattern_dir(patterns, "incomplete", with_skill_md=False)
    ids = skill_registry.all_ids()
    assert "incomplete" not in ids


# ── get ──────────────────────────────────────────────────────

def test_get_returns_auto_skill_entry(configured_grit_engine, tmp_skills_setup):
    entry = skill_registry.get("grit-rules")
    assert entry["type"] == "auto"
    assert entry["generator"] == "rules"


def test_get_returns_pattern_skill_entry(tmp_skills_setup):
    patterns, _ = tmp_skills_setup
    _make_pattern_dir(patterns, "api-bean")
    entry = skill_registry.get("api-bean")
    assert entry == {"type": "pattern", "procedure_id": "api-bean"}


def test_get_raises_on_unknown(tmp_skills_setup):
    with pytest.raises(KeyError):
        skill_registry.get("nonexistent-skill")


# ── skill_id_for_procedure ───────────────────────────────────

def test_skill_id_for_procedure_returns_matching_id(tmp_skills_setup):
    patterns, _ = tmp_skills_setup
    _make_pattern_dir(patterns, "caching-pattern")
    assert skill_registry.skill_id_for_procedure("caching-pattern") == "caching-pattern"


def test_skill_id_for_procedure_none_when_missing(tmp_skills_setup):
    assert skill_registry.skill_id_for_procedure("no-such-procedure") is None


# ── path helpers ─────────────────────────────────────────────

def test_deployed_path_composition(tmp_skills_setup):
    _, skills = tmp_skills_setup
    assert skill_registry.deployed_path("grit-rules") == os.path.join(
        str(skills), "grit-rules",
    )


def test_source_path_for_pattern(tmp_skills_setup):
    patterns, _ = tmp_skills_setup
    _make_pattern_dir(patterns, "p")
    assert skill_registry.source_path("p") == os.path.join(str(patterns), "p")


def test_deployed_exists_false_when_missing(tmp_skills_setup):
    patterns, _ = tmp_skills_setup
    _make_pattern_dir(patterns, "p")
    # Registered as a pattern, but deployed directory not created.
    assert skill_registry.deployed_exists("p") is False


def test_deployed_exists_true_when_skill_md_present(tmp_skills_setup):
    patterns, skills = tmp_skills_setup
    _make_pattern_dir(patterns, "p")
    (skills / "p").mkdir()
    (skills / "p" / "SKILL.md").write_text("deployed")
    assert skill_registry.deployed_exists("p") is True


def test_source_exists_pattern_requires_skill_md(tmp_skills_setup):
    patterns, _ = tmp_skills_setup
    _make_pattern_dir(patterns, "with-md")
    assert skill_registry.source_exists("with-md") is True


def test_source_exists_auto_always_true(configured_grit_engine, tmp_skills_setup):
    # 'grit-rules' is the built-in auto skill.
    assert skill_registry.source_exists("grit-rules") is True
