"""Unit tests for lib.skills.skill_deployer.

Covers id validation, path resolution, deployment (including the
shim+content.md split), discovery, and undeploy. Uses tmp_db so the
experiments lookup inside deploy_pattern_as_skill goes against an
empty DB.
"""

from __future__ import annotations

import os

import pytest

from lib.skills import skill_deployer
from lib.settings import settings


@pytest.fixture
def tmp_skills_dir(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setattr(settings, "skills_dir", str(skills))
    return skills


def _make_pattern_source(root, procedure_id, body="## Body\n\nguide body"):
    src = root / procedure_id
    src.mkdir(parents=True, exist_ok=True)
    (src / "SKILL.md").write_text(
        f"---\ntitle: {procedure_id}\nprocedure: {procedure_id}\n---\n{body}"
    )
    return src


# ── _validate_id ─────────────────────────────────────────────

def test_validate_id_accepts_lowercase_slug():
    skill_deployer._validate_id("my-skill")


def test_validate_id_rejects_uppercase():
    with pytest.raises(ValueError):
        skill_deployer._validate_id("My-Skill")


def test_validate_id_rejects_empty():
    with pytest.raises(ValueError):
        skill_deployer._validate_id("")


# ── get_skill_path + _resolve_base ───────────────────────────

def test_get_skill_path_uses_skills_dir_default(tmp_skills_dir):
    path = skill_deployer.get_skill_path("my-skill")
    assert path == str(tmp_skills_dir / "my-skill" / "SKILL.md")


def test_get_skill_path_honours_target_dir(tmp_skills_dir, tmp_path):
    override = tmp_path / "project-skills"
    path = skill_deployer.get_skill_path("my-skill", target_dir=str(override))
    assert path == str(override / "my-skill" / "SKILL.md")


# ── is_deployed ──────────────────────────────────────────────

def test_is_deployed_false_when_missing(tmp_skills_dir):
    assert skill_deployer.is_deployed("nope") is False


def test_is_deployed_true_when_skill_md_exists(tmp_skills_dir):
    (tmp_skills_dir / "s").mkdir()
    (tmp_skills_dir / "s" / "SKILL.md").write_text("stub")
    assert skill_deployer.is_deployed("s") is True


def test_is_deployed_invalid_id_returns_false(tmp_skills_dir):
    assert skill_deployer.is_deployed("Invalid") is False


# ── get_deployed_procedures ──────────────────────────────────

def test_get_deployed_procedures_empty(tmp_skills_dir):
    assert skill_deployer.get_deployed_procedures() == set()


def test_get_deployed_procedures_returns_dirs_with_skill_md(tmp_skills_dir):
    for name in ("alpha", "beta", "gamma"):
        d = tmp_skills_dir / name
        d.mkdir()
        (d / "SKILL.md").write_text("body")
    # Directory without SKILL.md is excluded.
    (tmp_skills_dir / "incomplete").mkdir()
    assert skill_deployer.get_deployed_procedures() == {"alpha", "beta", "gamma"}


def test_get_deployed_procedures_missing_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "skills_dir",
                        str(tmp_path / "nope"))
    assert skill_deployer.get_deployed_procedures() == set()


# ── deploy_pattern_as_skill ──────────────────────────────────

def test_deploy_pattern_writes_shim_and_content_md(
        tmp_db, tmp_skills_dir, tmp_path):
    src = _make_pattern_source(tmp_path, "api-bean", body="## Disciplines\n\n- x")
    deployed = skill_deployer.deploy_pattern_as_skill(
        str(src), "api-bean", "API Bean",
    )
    # Shim + content both exist.
    skill_dir = tmp_skills_dir / "api-bean"
    shim = skill_dir / "SKILL.md"
    content = skill_dir / "content.md"
    assert shim.exists()
    assert content.exists()
    assert str(deployed) == str(shim)
    # Shim points at the absolute content.md path.
    shim_text = shim.read_text()
    assert "content.md" in shim_text
    assert "description: \"API Bean - procedure guide from regin\"" in shim_text
    assert f"name: api-bean" in shim_text
    # Content body doesn't have frontmatter.
    assert "---" not in content.read_text()
    assert "## Disciplines" in content.read_text()


def test_deploy_pattern_uses_frontmatter_description(
        tmp_db, tmp_skills_dir, tmp_path):
    src = _make_pattern_source(tmp_path, "topic-router")
    skill_md = src / "SKILL.md"
    skill_md.write_text(
        '---\n'
        'title: Topic Router\n'
        'description: "Read before every agent task."\n'
        'procedure: topic-router\n'
        '---\n'
        '## Body\n\nbody'
    )

    skill_deployer.deploy_pattern_as_skill(str(src), "topic-router", "Topic Router")

    shim_text = (tmp_skills_dir / "topic-router" / "SKILL.md").read_text()
    assert 'description: "Read before every agent task."' in shim_text


def test_deploy_pattern_preserves_multiline_description(
        tmp_db, tmp_skills_dir, tmp_path):
    """Regression: YAML block scalars must be parsed in full and collapsed for the shim."""
    src = _make_pattern_source(tmp_path, "topic-router")
    skill_md = src / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "title: Topic Router\n"
        "description: 'Run this before any repo task. Triggers: fix a bug, debug, \"how does\n"
        "  X work?\", understand a procedure, explain code. It routes 2-6 keywords through\n"
        "  the dense pattern index.'\n"
        "procedure: topic-router\n"
        "---\n"
        "## Body\n\nbody"
    )

    skill_deployer.deploy_pattern_as_skill(str(src), "topic-router", "Topic Router")

    shim_text = (tmp_skills_dir / "topic-router" / "SKILL.md").read_text()
    # Full description survives (last clause was on line 3 of the YAML block).
    assert "dense pattern index." in shim_text
    # The shim's description sits on a single line — no raw newlines leak in.
    desc_line = [ln for ln in shim_text.splitlines() if ln.startswith("description:")]
    assert len(desc_line) == 1
    assert "X work?" in desc_line[0]
    # Embedded double quotes are escaped, not unbalanced.
    assert '\\"how does' in desc_line[0]


def test_deploy_pattern_copies_sibling_files(
        tmp_db, tmp_skills_dir, tmp_path):
    src = _make_pattern_source(tmp_path, "api-bean")
    refs = src / "references"
    refs.mkdir()
    (refs / "note.md").write_text("notes")
    skill_deployer.deploy_pattern_as_skill(str(src), "api-bean", "API Bean")
    assert (tmp_skills_dir / "api-bean" / "references" / "note.md").exists()


def test_deploy_pattern_missing_source_raises(tmp_skills_dir, tmp_path):
    with pytest.raises(FileNotFoundError):
        skill_deployer.deploy_pattern_as_skill(
            str(tmp_path / "missing"), "api-bean", "API Bean",
        )


def test_deploy_pattern_invalid_id_raises(tmp_skills_dir, tmp_path):
    src = _make_pattern_source(tmp_path, "api-bean")
    with pytest.raises(ValueError):
        skill_deployer.deploy_pattern_as_skill(str(src), "INVALID", "X")


def test_deploy_pattern_rebuilds_existing(
        tmp_db, tmp_skills_dir, tmp_path):
    """Re-deploying with different source content should overwrite
    — old files that no longer exist must disappear."""
    src1 = _make_pattern_source(tmp_path / "first", "x")
    (src1 / "old-extra.md").write_text("old")
    skill_deployer.deploy_pattern_as_skill(str(src1), "x", "Title")
    assert (tmp_skills_dir / "x" / "old-extra.md").exists()

    # Second source has no old-extra.md
    src2 = _make_pattern_source(tmp_path / "second", "x",
                                  body="## Body\n\nnew")
    skill_deployer.deploy_pattern_as_skill(str(src2), "x", "Title")
    assert not (tmp_skills_dir / "x" / "old-extra.md").exists()


# ── undeploy_skill ──────────────────────────────────────────

def test_undeploy_removes_directory(tmp_skills_dir):
    d = tmp_skills_dir / "victim"
    d.mkdir()
    (d / "SKILL.md").write_text("body")
    assert skill_deployer.undeploy_skill("victim") is True
    assert not d.exists()


def test_undeploy_missing_returns_false(tmp_skills_dir):
    assert skill_deployer.undeploy_skill("nothere") is False


def test_undeploy_invalid_id_raises(tmp_skills_dir):
    with pytest.raises(ValueError):
        skill_deployer.undeploy_skill("Invalid")


# ── _display_path ───────────────────────────────────────────

def test_display_path_collapses_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = os.path.expanduser("~")
    assert skill_deployer._display_path(home) == "~"
    assert skill_deployer._display_path(
        os.path.join(home, "foo", "bar")
    ) == "~/foo/bar"


def test_display_path_leaves_non_home_untouched():
    assert skill_deployer._display_path("/tmp/elsewhere") == "/tmp/elsewhere"


# ── deploy_rules_index_skill ────────────────────────────────

def test_deploy_rules_index_skill_writes_shim_and_content_md(configured_grit_engine, tmp_skills_dir, tmp_path):
    rules_md = tmp_path / "RULES.md"
    rules_md.write_text("# Rules\n\n- rule-one: a\n- rule-two: b\n")

    skill_path = skill_deployer.deploy_rules_index_skill(str(rules_md))

    skill_dir = tmp_skills_dir / skill_deployer.RULES_INDEX_SKILL_ID
    assert str(skill_path) == str(skill_dir / "SKILL.md")
    assert (skill_dir / "SKILL.md").exists()
    content = (skill_dir / "content.md").read_text()
    assert "rule-one" in content
    assert "rule-two" in content

    shim = (skill_dir / "SKILL.md").read_text()
    assert f"name: {skill_deployer.RULES_INDEX_SKILL_ID}" in shim
    assert "content.md" in shim


def test_deploy_rules_index_skill_missing_source_raises(
        tmp_skills_dir, tmp_path):
    with pytest.raises(FileNotFoundError):
        skill_deployer.deploy_rules_index_skill(
            str(tmp_path / "missing.md")
        )


def test_deploy_rules_index_skill_overwrites_existing_grit_dir(configured_grit_engine, tmp_skills_dir, tmp_path, monkeypatch):
    """The function wipes and re-copies the .grit dest on re-deploy; make
    sure that path is exercised without blowing up."""
    rules_md = tmp_path / "R.md"
    rules_md.write_text("# R")

    # Pre-seed an .grit/ dir next to where the re-deploy will land.
    skill_dir = tmp_skills_dir / skill_deployer.RULES_INDEX_SKILL_ID
    skill_dir.mkdir()
    (skill_dir / ".grit").mkdir()
    (skill_dir / ".grit" / "old.txt").write_text("stale")

    skill_deployer.deploy_rules_index_skill(str(rules_md))

    # Whether .grit got re-copied depends on the real repo's .grit
    # existing (non-test environments); we only assert the old marker
    # is gone, which proves the wipe-and-recopy path ran.
    assert not (skill_dir / ".grit" / "old.txt").exists()
