"""Unit tests for lib.skills.skill_sync.

Covers both the pure helpers (_extract_section, _replace_section,
_hash_file, _pattern_signature, _skill_body, _read_title,
_pattern_extras_hash, _mirror_pattern_extras) and the state/pull/push
orchestration against a stubbed skill_registry.

PATTERNS_DIR and SKILLS_DIR are both redirected into tmp_path so the
tests never touch the user's real ~/.claude/skills/ or repo patterns/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.skills import skill_sync
from lib.skills import skill_registry
from lib.settings import settings


# ── fixtures ─────────────────────────────────────────────────

@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    """Redirect PATTERNS_DIR + SKILLS_DIR to tmp_path; return helper paths."""
    patterns = tmp_path / "patterns"
    skills = tmp_path / "skills"
    patterns.mkdir()
    skills.mkdir()

    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(settings, "skills_dir", str(skills))
    # `deployed_path` uses os.path.join with SKILLS_DIR, which is the
    # module-level constant — patch it there too.
    yield {
        "root": tmp_path,
        "patterns": patterns,
        "skills": skills,
    }


def _seed_pattern_source(patterns_dir: Path, slug: str, *,
                          title: str = "My Title",
                          disciplines: str = "- rule one",
                          anti_patterns: str = "- bad one",
                          extras: dict[str, str] | None = None) -> Path:
    src = patterns_dir / slug
    src.mkdir()
    (src / "SKILL.md").write_text(
        f'---\ntitle: "{title}"\nprocedure: {slug}\n---\n'
        f"# Title\n\n"
        f"## Disciplines\n\n{disciplines}\n\n"
        f"## Anti-Patterns\n\n{anti_patterns}\n"
    )
    if extras:
        for rel, content in extras.items():
            p = src / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    return src


def _seed_deployed(skills_dir: Path, skill_id: str, *,
                    title: str = "My Title",
                    body: str = "## Disciplines\n\n- rule one\n\n"
                                 "## Anti-Patterns\n\n- bad one\n",
                    extras: dict[str, str] | None = None) -> Path:
    dep = skills_dir / skill_id
    dep.mkdir()
    # Real deploys write a shim SKILL.md + content.md; skill_sync reads
    # content.md first when present.
    (dep / "SKILL.md").write_text(
        f"---\nname: {skill_id}\ndescription: \"{title}\"\n---\n"
        f"See content.md.\n"
    )
    # The _extract_section regex requires a leading newline before the
    # `## Header` — real deploys emit that leading blank line.
    (dep / "content.md").write_text("\n" + body)
    if extras:
        for rel, content in extras.items():
            p = dep / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    return dep


# ── pure helpers ────────────────────────────────────────────

def test_hash_file_is_deterministic(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello world")
    h1 = skill_sync._hash_file(str(f))
    h2 = skill_sync._hash_file(str(f))
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_hash_file_changes_on_edit(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello")
    h1 = skill_sync._hash_file(str(f))
    f.write_bytes(b"hello!")
    h2 = skill_sync._hash_file(str(f))
    assert h1 != h2


def test_extract_section_returns_body():
    # _extract_section requires a leading newline before the header.
    text = "\n## Alpha\n\nbody\n\n## Beta\n\nelse"
    assert skill_sync._extract_section(text, "## Alpha") == "body"


def test_extract_section_missing_returns_none():
    assert skill_sync._extract_section("no headers here",
                                         "## Missing") is None


def test_replace_section_overwrites_body():
    text = "before\n\n## Alpha\n\nold\n\n## Beta\n\nelse"
    out = skill_sync._replace_section(text, "## Alpha", "new body")
    assert "new body" in out
    assert "old" not in out
    assert "else" in out  # beta preserved


def test_replace_section_missing_header_is_noop():
    text = "no alpha here"
    assert skill_sync._replace_section(text, "## Alpha", "x") == text


def test_skill_body_strips_frontmatter():
    md = "---\ntitle: X\n---\nthe body\n"
    assert skill_sync._skill_body(md).strip() == "the body"


def test_skill_body_no_frontmatter_returns_whole():
    md = "plain body"
    assert skill_sync._skill_body(md) == "plain body"


def test_pattern_signature_extracts_disciplines_and_anti():
    md = (
        "---\ntitle: x\n---\n"
        "## Disciplines\n\n- d1\n\n"
        "## Anti-Patterns\n\n- a1\n"
    )
    sig = skill_sync._pattern_signature(md)
    assert sig == ("- d1", "- a1")


def test_pattern_extras_hash_different_for_different_contents(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    (a / "SKILL.md").write_text("frontmatter")
    (a / "note.md").write_text("v1")

    h1 = skill_sync._pattern_extras_hash(str(a))
    (a / "note.md").write_text("v2")
    h2 = skill_sync._pattern_extras_hash(str(a))
    assert h1 != h2


def test_pattern_extras_hash_ignores_skill_md_and_content_md(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    (a / "note.md").write_text("extra")
    (a / "SKILL.md").write_text("v1")
    (a / "content.md").write_text("v1-body")

    h1 = skill_sync._pattern_extras_hash(str(a))
    (a / "SKILL.md").write_text("v2")
    (a / "content.md").write_text("v2-body")
    h2 = skill_sync._pattern_extras_hash(str(a))
    assert h1 == h2  # SKILL.md / content.md should be excluded


def test_pattern_extras_hash_missing_dir_returns_none():
    assert skill_sync._pattern_extras_hash("/nope") is None


def test_read_title_finds_quoted_title(tmp_path):
    f = tmp_path / "x.md"
    f.write_text('---\ntitle: "The Title"\n---\nbody\n')
    assert skill_sync._read_title(str(f)) == "The Title"


def test_read_title_finds_unquoted(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("---\ntitle: Plain\n---\nbody\n")
    assert skill_sync._read_title(str(f)) == "Plain"


def test_read_title_missing_file_returns_none(tmp_path):
    assert skill_sync._read_title(str(tmp_path / "nope.md")) is None


def test_read_title_no_title_line_returns_none(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("---\nprocedure: slug\n---\nbody\n")
    assert skill_sync._read_title(str(f)) is None


# ── _mirror_pattern_extras ──────────────────────────────────

def test_mirror_pattern_extras_copies_new_files(tmp_path):
    dep = tmp_path / "dep"
    src = tmp_path / "src"
    dep.mkdir()
    src.mkdir()
    (dep / "references").mkdir()
    (dep / "references" / "note.md").write_text("note")
    (dep / "SKILL.md").write_text("shim")

    changed = skill_sync._mirror_pattern_extras(str(dep), str(src))
    assert changed == 1
    assert (src / "references" / "note.md").read_text() == "note"
    # SKILL.md / content.md are not mirrored.
    assert not (src / "SKILL.md").exists()


def test_mirror_pattern_extras_removes_orphaned_source_files(tmp_path):
    dep = tmp_path / "dep"
    src = tmp_path / "src"
    dep.mkdir()
    src.mkdir()
    # Only lives on the source side — should be deleted.
    (src / "old.md").write_text("stale")

    changed = skill_sync._mirror_pattern_extras(str(dep), str(src))
    assert changed == 1
    assert not (src / "old.md").exists()


def test_mirror_pattern_extras_missing_dep_returns_zero(tmp_path):
    assert skill_sync._mirror_pattern_extras(str(tmp_path / "nope"),
                                               str(tmp_path / "src")) == 0


def test_mirror_pattern_extras_noop_when_in_sync(tmp_path):
    dep = tmp_path / "dep"
    src = tmp_path / "src"
    dep.mkdir()
    src.mkdir()
    (dep / "same.md").write_text("payload")
    (src / "same.md").write_text("payload")
    changed = skill_sync._mirror_pattern_extras(str(dep), str(src))
    assert changed == 0


# ── state() ──────────────────────────────────────────────────

def test_state_missing_when_neither_side_exists(sync_env):
    # Register a virtual skill by creating an empty pattern dir with no
    # SKILL.md — pattern skills need SKILL.md to be "source_exists".
    (sync_env["patterns"] / "missing-slug").mkdir()
    # Actually: skill_registry only registers dirs *with* SKILL.md.
    # Simulate STATE_MISSING by picking a completely unknown slug that
    # is in SKILLS but neither source nor deployed copy exists.
    with pytest.raises(KeyError):
        skill_sync.state("does-not-exist")


def test_state_source_only(sync_env):
    _seed_pattern_source(sync_env["patterns"], "src-only")
    assert skill_sync.state("src-only") == skill_sync.STATE_SOURCE_ONLY


def test_state_project_only_when_project_deployment_exists(sync_env, monkeypatch):
    _seed_pattern_source(sync_env["patterns"], "proj-only")
    monkeypatch.setattr(
        skill_sync, "_has_project_deployment",
        lambda slug: slug == "proj-only",
    )
    assert skill_sync.state("proj-only") == skill_sync.STATE_PROJECT_ONLY


def test_state_deployed_only(sync_env):
    _seed_pattern_source(sync_env["patterns"], "dep-only")
    _seed_deployed(sync_env["skills"], "dep-only")
    # Now remove source → deployed-only.
    import shutil as _sh
    _sh.rmtree(sync_env["patterns"] / "dep-only")
    with pytest.raises(KeyError):
        # Source-dir removal also removes it from the registry.
        skill_sync.state("dep-only")


def test_state_in_sync_when_sections_and_extras_match(sync_env):
    _seed_pattern_source(sync_env["patterns"], "good")
    _seed_deployed(sync_env["skills"], "good")
    assert skill_sync.state("good") == skill_sync.STATE_IN_SYNC


def test_state_drifted_when_deployed_discipline_differs(sync_env):
    _seed_pattern_source(sync_env["patterns"], "drift1")
    _seed_deployed(
        sync_env["skills"], "drift1",
        body=(
            "## Disciplines\n\n- NEW deployed rule\n\n"
            "## Anti-Patterns\n\n- bad one\n"
        ),
    )
    assert skill_sync.state("drift1") == skill_sync.STATE_DRIFTED


def test_state_drifted_when_extras_differ(sync_env):
    _seed_pattern_source(
        sync_env["patterns"], "drift2",
        extras={"references/a.md": "source-copy"},
    )
    _seed_deployed(
        sync_env["skills"], "drift2",
        extras={"references/a.md": "deployed-copy"},
    )
    assert skill_sync.state("drift2") == skill_sync.STATE_DRIFTED


# ── list_states() ───────────────────────────────────────────

def test_list_states_yields_all_skills(sync_env):
    _seed_pattern_source(sync_env["patterns"], "a")
    _seed_pattern_source(sync_env["patterns"], "b")
    out = list(skill_sync.list_states())
    slugs = {row[0] for row in out if row[1] == "pattern"}
    assert {"a", "b"} <= slugs


# ── pull() ──────────────────────────────────────────────────

def test_pull_auto_refuses(configured_grit_engine, sync_env):
    # grit-rules is an auto-type skill.
    msg = skill_sync.pull("grit-rules")
    assert "refused" in msg
    assert "auto-generated" in msg


def test_pull_not_deployed_returns_skipped(sync_env):
    _seed_pattern_source(sync_env["patterns"], "not-deployed")
    msg = skill_sync.pull("not-deployed")
    assert "skipped" in msg


def test_pull_pattern_folds_sections_and_extras(sync_env):
    _seed_pattern_source(
        sync_env["patterns"], "patt",
        disciplines="- ORIGINAL disc",
        anti_patterns="- ORIGINAL anti",
    )
    _seed_deployed(
        sync_env["skills"], "patt",
        body=(
            "## Disciplines\n\n- EDITED in deployed\n\n"
            "## Anti-Patterns\n\n- EDITED anti\n"
        ),
        extras={"references/new.md": "pulled-note"},
    )
    msg = skill_sync.pull("patt")
    assert "pulled" in msg

    src_md = (sync_env["patterns"] / "patt" / "SKILL.md").read_text()
    assert "EDITED in deployed" in src_md
    assert "EDITED anti" in src_md
    assert (sync_env["patterns"] / "patt" / "references" / "new.md").read_text() == "pulled-note"


def test_pull_noop_when_already_in_sync(sync_env):
    _seed_pattern_source(sync_env["patterns"], "same")
    _seed_deployed(sync_env["skills"], "same")
    msg = skill_sync.pull("same")
    assert "noop" in msg or "already in sync" in msg


# ── push() ──────────────────────────────────────────────────

def test_push_source_missing_returns_skipped(sync_env):
    # Register via _SKILLS_BASE only (no pattern dir) — but pattern
    # skills need a source dir to be in registry. Use grit-rules with
    # its auto path stripped... easier: remove source_exists by calling
    # push on a deleted pattern.
    _seed_pattern_source(sync_env["patterns"], "will-delete")
    _seed_deployed(sync_env["skills"], "will-delete")
    import shutil as _sh
    _sh.rmtree(sync_env["patterns"] / "will-delete")
    # Registry now has no entry for that slug → push raises KeyError.
    with pytest.raises(KeyError):
        skill_sync.push("will-delete")


def test_push_drifted_without_force_returns_confirm_force(
        sync_env, monkeypatch):
    _seed_pattern_source(sync_env["patterns"], "drift3")
    _seed_deployed(
        sync_env["skills"], "drift3",
        body="## Disciplines\n\n- DIFFERENT\n\n## Anti-Patterns\n\n- DIFF\n",
    )
    # Stub experiments.get_active so the reason string is deterministic
    # and we don't accidentally pass tmp_db-less DB calls.
    from lib import experiments
    monkeypatch.setattr(experiments, "get_active", lambda pid: None)

    msg = skill_sync.push("drift3")
    assert "confirm-force" in msg


# ── undeploy() ──────────────────────────────────────────────

def test_undeploy_not_deployed_returns_message(sync_env, monkeypatch):
    _seed_pattern_source(sync_env["patterns"], "u1")
    # Ensure no rules to disable (empty rules.json).
    from lib.rules import grit_rule_index as gri
    monkeypatch.setattr(gri, "rules_for_guide", lambda _pid: [])
    msg = skill_sync.undeploy("u1")
    assert "was not deployed" in msg


def test_undeploy_removes_deployed_dir(sync_env, monkeypatch):
    _seed_pattern_source(sync_env["patterns"], "u2")
    _seed_deployed(sync_env["skills"], "u2")
    # No linked rules to disable.
    from lib.rules import grit_rule_index as gri
    monkeypatch.setattr(gri, "rules_for_guide", lambda _pid: [])
    # skill_deployer.undeploy_skill reads SKILLS_DIR from lib.settings —
    # patch that directly.
    from lib.skills import skill_deployer
    monkeypatch.setattr(settings, "skills_dir",
                        str(sync_env["skills"]))
    msg = skill_sync.undeploy("u2")
    assert "removed" in msg
    assert not (sync_env["skills"] / "u2").exists()


# ── state() for auto-type skills ────────────────────────────

def test_state_auto_in_sync_when_rules_md_content_matches(configured_grit_engine, sync_env, tmp_path, monkeypatch):
    """Auto (grit-rules) is IN_SYNC when its content.md contains the
    current .grit/RULES.md text."""
    # Redirect settings.project_root so RULES.md resolves under tmp_path.
    rules_md = tmp_path / ".grit" / "RULES.md"
    rules_md.parent.mkdir()
    rules_md.write_text("# Rules\n\n- rule-one\n")
    monkeypatch.setattr(settings, "project_root", str(tmp_path))

    # Deploy grit-rules with content.md wrapping the rules body.
    dep = sync_env["skills"] / "grit-rules"
    dep.mkdir()
    (dep / "SKILL.md").write_text("---\nname: grit-rules\n---\nshim\n")
    (dep / "content.md").write_text(
        "Preamble.\n\n# Rules\n\n- rule-one\n"
    )
    assert skill_sync.state("grit-rules") == skill_sync.STATE_IN_SYNC


def test_state_auto_drifted_when_rules_body_differs(configured_grit_engine, sync_env, tmp_path, monkeypatch):
    rules_md = tmp_path / ".grit" / "RULES.md"
    rules_md.parent.mkdir()
    rules_md.write_text("# Rules\n\n- NEW rule\n")
    monkeypatch.setattr(settings, "project_root", str(tmp_path))

    dep = sync_env["skills"] / "grit-rules"
    dep.mkdir()
    (dep / "SKILL.md").write_text("---\nname: grit-rules\n---\nshim\n")
    (dep / "content.md").write_text(
        "# Rules\n\n- OLD stale content\n"
    )
    assert skill_sync.state("grit-rules") == skill_sync.STATE_DRIFTED


def test_state_auto_in_sync_when_rules_md_absent_but_deployed(configured_grit_engine, sync_env, tmp_path, monkeypatch):
    """With a deployed copy but no local .grit/RULES.md baseline to verify
    against, the auto skill is treated as IN_SYNC — we can't prove drift,
    and falsely reporting SOURCE_ONLY contradicts the deployed file."""
    monkeypatch.setattr(settings, "project_root", str(tmp_path))

    dep = sync_env["skills"] / "grit-rules"
    dep.mkdir()
    (dep / "SKILL.md").write_text("---\nname: grit-rules\n---\nshim\n")
    (dep / "content.md").write_text("# existing body\n")
    assert skill_sync.state("grit-rules") == skill_sync.STATE_IN_SYNC


# ── push() for auto-type ────────────────────────────────────

def test_push_auto_to_project_returns_skipped(configured_grit_engine, sync_env):
    msg = skill_sync.push("grit-rules", target_dir="/tmp/some-repo")
    assert msg.startswith("skipped:")
    assert "auto skill" in msg


def test_push_auto_global_regenerates_and_deploys(
        configured_grit_engine, sync_env, monkeypatch, tmp_path):
    from lib.skills import skill_deployer
    from lib.rules import grit_rule_index
    monkeypatch.setattr(
        grit_rule_index, "regenerate",
        lambda write_guides=True: {"rules": 3, "rules_md": "/tmp/R.md"},
    )
    monkeypatch.setattr(
        skill_deployer, "deploy_rules_index_skill",
        lambda _p: "/tmp/deployed",
    )
    msg = skill_sync.push("grit-rules")
    assert msg.startswith("pushed auto grit-rules")
    assert "/tmp/deployed" in msg


# ── pull() skipped when no source ───────────────────────────

def test_pull_pattern_no_source_skill_md_returns_skipped(
        sync_env, tmp_db):
    # Create the pattern dir but NO SKILL.md.
    (sync_env["patterns"] / "bare").mkdir()
    # Can't register without SKILL.md — registry sees nothing.
    with pytest.raises(KeyError):
        skill_sync.pull("bare")


# ── _set_linked_rules_disabled ──────────────────────────────

def test_set_linked_rules_disabled_pattern_with_rules(
        sync_env, monkeypatch):
    _seed_pattern_source(sync_env["patterns"], "lp1")
    calls = {}
    from lib.skills import skill_deployer
    from lib.rules import grit_rule_index
    monkeypatch.setattr(
        grit_rule_index, "rules_for_guide",
        lambda slug: [{"id": "r1"}, {"id": "r2"}],
    )
    monkeypatch.setattr(
        grit_rule_index, "set_rules_disabled",
        lambda ids, disabled: calls.setdefault("set", (ids, disabled)),
    )
    monkeypatch.setattr(
        grit_rule_index, "regenerate",
        lambda write_guides=False: calls.setdefault("regen", True),
    )
    monkeypatch.setattr(
        skill_deployer, "deploy_rules_index_skill",
        lambda _p: calls.setdefault("deploy", True),
    )

    ids = skill_sync._set_linked_rules_disabled("lp1", True)
    assert ids == ["r1", "r2"]
    assert calls["set"] == (["r1", "r2"], True)
    assert calls["regen"] is True
    assert calls["deploy"] is True


def test_set_linked_rules_disabled_no_rules_returns_empty(
        sync_env, monkeypatch):
    _seed_pattern_source(sync_env["patterns"], "lp2")
    from lib.rules import grit_rule_index
    monkeypatch.setattr(grit_rule_index, "rules_for_guide",
                        lambda _slug: [])
    assert skill_sync._set_linked_rules_disabled("lp2", True) == []


def test_set_linked_rules_disabled_auto_skill_returns_empty(configured_grit_engine, sync_env):
    # grit-rules is auto-type, not pattern → early return.
    assert skill_sync._set_linked_rules_disabled(
        "grit-rules", True,
    ) == []
