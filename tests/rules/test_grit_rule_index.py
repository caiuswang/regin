"""Unit tests for lib.rules.grit_rule_index.

Covers the @rule parser, rules.json / RULES.md emitters, disable-list
state file, delete/update-rule file mutators, and the verification-
section stripper.

All file-system paths (GRIT_PATTERNS_DIR, RULES_JSON_PATH,
RULES_MD_PATH, DISABLED_RULES_PATH, PROJECT_ROOT, PATTERNS_DIR) are
monkeypatched into tmp_path so tests never touch the user's repo.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lib.rules import grit_rule_index as gri
from lib.settings import settings


# ── fixtures ─────────────────────────────────────────────────

@pytest.fixture
def grit_env(tmp_path, monkeypatch):
    """Install isolated paths for every grit_rule_index target file.

    Note: write_rules_json/write_rules_md take path as a default
    argument, which is captured at module import time. Monkeypatching
    the module constant isn't enough — we also override the bound
    default via __defaults__ so callers that omit `path` still land
    in tmp_path.
    """
    grit_patterns = tmp_path / ".grit" / "patterns" / "java"
    grit_patterns.mkdir(parents=True)
    patterns = tmp_path / "patterns"
    patterns.mkdir()
    rules_json = str(tmp_path / ".grit" / "rules.json")
    rules_md = str(tmp_path / ".grit" / "RULES.md")

    monkeypatch.setattr(gri, "GRIT_PATTERNS_DIR", str(grit_patterns))
    monkeypatch.setattr(gri, "RULES_JSON_PATH", rules_json)
    monkeypatch.setattr(gri, "RULES_MD_PATH", rules_md)
    monkeypatch.setattr(gri, "DISABLED_RULES_PATH",
                        str(tmp_path / ".grit" / "rules_disabled.txt"))
    monkeypatch.setattr(settings, "project_root", str(tmp_path))
    monkeypatch.setattr(settings, "patterns_dir", str(patterns))

    # Rebind function defaults so path= implicit calls also land here.
    monkeypatch.setattr(gri.write_rules_json, "__defaults__", (rules_json,))
    monkeypatch.setattr(gri.write_rules_md, "__defaults__", (rules_md,))

    yield {
        "root": tmp_path,
        "grit_patterns": grit_patterns,
        "patterns": patterns,
        "rules_json": Path(rules_json),
        "rules_md": Path(rules_md),
        "disabled": Path(gri.DISABLED_RULES_PATH),
    }


def _write_grit_rule(
    dir_path: Path, filename: str, rule_id: str = "example_rule",
    *, layer: str = "entity", triggers: str = "*Entity.java",
    severity: str = "error", guide: str = "entity-pattern",
    summary: str = "A rule.",
    body: str = "{ class_declaration(name=$n) }",
    extra_patterns: list[dict] | None = None,
) -> Path:
    """Write a minimal valid .grit file."""
    lines = [
        f"// @rule id={rule_id}",
        f"// @rule layer={layer}",
        f"// @rule triggers={triggers}",
        f"// @rule severity={severity}",
        f"// @rule guide={guide}",
        f"// @rule summary={summary}",
        f"pattern {rule_id}() {body}",
    ]
    if extra_patterns:
        for extra in extra_patterns:
            # Blank line separates rule blocks — delete_rule uses
            # '\n\n' as its backward scan boundary.
            lines.append("")
            for k in ("id", "layer", "triggers", "severity", "guide", "summary"):
                if k in extra:
                    lines.append(f"// @rule {k}={extra[k]}")
            lines.append(
                f"pattern {extra.get('id', 'extra')}() "
                f"{extra.get('body', '{ class_declaration(name=$x) }')}"
            )
    path = dir_path / filename
    path.write_text("\n".join(lines) + "\n")
    return path


# ── parse_grit_rules ─────────────────────────────────────────

def test_parse_grit_rules_minimal(grit_env):
    _write_grit_rule(grit_env["grit_patterns"], "rule.grit")
    rules = gri.parse_grit_rules()
    assert len(rules) == 1
    r = rules[0]
    assert r["id"] == "example_rule"
    assert r["layer"] == "entity"
    assert r["triggers"] == ["*Entity.java"]
    assert r["severity"] == "error"
    assert r["guide"] == "entity-pattern"
    assert r["summary"] == "A rule."
    assert r["source_file"].endswith("rule.grit")


def test_parse_grit_rules_multiple_triggers(grit_env):
    _write_grit_rule(grit_env["grit_patterns"], "two.grit",
                     triggers="*Entity.java, @Table")
    rules = gri.parse_grit_rules()
    assert rules[0]["triggers"] == ["*Entity.java", "@Table"]


def test_parse_grit_rules_mismatched_id_raises(grit_env):
    # Header says id=alpha but pattern is named beta.
    f = grit_env["grit_patterns"] / "bad.grit"
    f.write_text(
        "// @rule id=alpha\n"
        "// @rule layer=entity\n"
        "// @rule triggers=*Entity.java\n"
        "// @rule severity=error\n"
        "// @rule guide=g\n"
        "// @rule summary=s\n"
        "pattern beta() { class_declaration(name=$n) }\n"
    )
    with pytest.raises(gri.RuleMetadataError):
        gri.parse_grit_rules()


def test_parse_grit_rules_skips_pattern_without_full_header(grit_env):
    """Incomplete headers should drop the pattern silently."""
    f = grit_env["grit_patterns"] / "partial.grit"
    f.write_text(
        "// @rule id=example\n"
        "pattern example() { class_declaration(name=$n) }\n"
    )
    assert gri.parse_grit_rules() == []


def test_parse_grit_rules_empty_dir_returns_empty(grit_env):
    assert gri.parse_grit_rules() == []


def test_parse_grit_rules_helper_pattern_resets_pending(grit_env):
    """A non-comment code line between two @rule blocks must discard
    the first pending block so it doesn't bleed into the second."""
    f = grit_env["grit_patterns"] / "interleave.grit"
    f.write_text(
        "// @rule id=first\n"
        "// @rule layer=entity\n"
        "helper_code_line(with_no_pattern_decl)\n"  # non-@ non-comment
        "// @rule id=second\n"
        "// @rule layer=entity\n"
        "// @rule triggers=*.java\n"
        "// @rule severity=error\n"
        "// @rule guide=g\n"
        "// @rule summary=s\n"
        "pattern second() { class_declaration(name=$n) }\n"
    )
    rules = gri.parse_grit_rules()
    ids = [r["id"] for r in rules]
    assert "first" not in ids  # pending was cleared
    assert "second" in ids


# ── missing_metadata ─────────────────────────────────────────

def test_missing_metadata_flags_incomplete_rules(grit_env):
    f = grit_env["grit_patterns"] / "partial.grit"
    f.write_text(
        "// @rule id=partial\n"
        "// @rule layer=entity\n"
        "pattern partial() { class_declaration(name=$n) }\n"
    )
    missing = gri.missing_metadata()
    assert len(missing) == 1
    rel, name, fields = missing[0]
    assert rel.endswith("partial.grit")
    assert name == "partial"
    assert set(fields) >= {"triggers", "severity", "guide", "summary"}


def test_missing_metadata_clean_rules_yield_nothing(grit_env):
    _write_grit_rule(grit_env["grit_patterns"], "good.grit")
    assert gri.missing_metadata() == []


# ── disabled-rule list ───────────────────────────────────────

def test_load_disabled_rule_ids_missing_file_returns_empty(grit_env):
    assert gri.load_disabled_rule_ids() == set()


def test_set_rules_disabled_adds_and_removes(grit_env):
    out = gri.set_rules_disabled(["r1", "r2"], disabled=True)
    assert out == {"r1", "r2"}
    # Re-read from file.
    assert gri.load_disabled_rule_ids() == {"r1", "r2"}

    out2 = gri.set_rules_disabled(["r1"], disabled=False)
    assert out2 == {"r2"}
    assert gri.load_disabled_rule_ids() == {"r2"}


def test_load_disabled_rule_ids_strips_comments(grit_env):
    grit_env["disabled"].parent.mkdir(parents=True, exist_ok=True)
    grit_env["disabled"].write_text(
        "# a header comment\n"
        "keep-me\n"
        "# another\n"
        "\n"
        "trailing # inline comment\n"
    )
    out = gri.load_disabled_rule_ids()
    assert out == {"keep-me", "trailing"}


# ── write_rules_json / load_rules_index / rules_for_guide ────

def test_write_and_load_rules_json_roundtrip(grit_env):
    rules = [
        {"id": "a", "layer": "entity", "triggers": ["*.java"],
         "severity": "error", "guide": "g1", "summary": "s",
         "source_file": ".grit/a.grit"},
        {"id": "b", "layer": "controller", "triggers": ["*.java", "@Get"],
         "severity": "warn", "guide": "g2", "summary": "s",
         "source_file": ".grit/b.grit"},
    ]
    gri.write_rules_json(rules)
    data = gri.load_rules_index()
    assert data["version"] == 1
    assert len(data["rules"]) == 2
    assert data["by_layer"] == {"controller": ["b"], "entity": ["a"]}
    assert data["by_guide"] == {"g1": ["a"], "g2": ["b"]}
    assert data["by_trigger"]["*.java"] == ["a", "b"]
    assert data["by_trigger"]["@Get"] == ["b"]


def test_load_rules_index_returns_empty_shape_when_missing(grit_env):
    data = gri.load_rules_index()
    assert data == {"version": 1, "rules": [], "by_layer": {},
                    "by_trigger": {}, "by_guide": {}}


def test_rules_for_guide_matches(grit_env):
    rules = [
        {"id": "a", "layer": "entity", "triggers": ["*.java"],
         "severity": "error", "guide": "svc", "summary": "s",
         "source_file": "a"},
        {"id": "b", "layer": "entity", "triggers": ["*.java"],
         "severity": "error", "guide": "other", "summary": "s",
         "source_file": "b"},
    ]
    gri.write_rules_json(rules)
    out = gri.rules_for_guide("svc")
    assert [r["id"] for r in out] == ["a"]


def test_rules_for_guide_returns_empty_when_no_match(grit_env):
    gri.write_rules_json([])
    assert gri.rules_for_guide("nope") == []


# ── write_rules_md ──────────────────────────────────────────

def test_write_rules_md_groups_by_layer(grit_env):
    rules = [
        {"id": "a", "layer": "entity", "triggers": ["*Entity.java"],
         "severity": "error", "guide": "g", "summary": "s",
         "source_file": "a"},
        {"id": "b", "layer": "controller", "triggers": ["*Controller.java"],
         "severity": "warn", "guide": "g", "summary": "s",
         "source_file": "b"},
    ]
    gri.write_rules_md(rules)
    text = grit_env["rules_md"].read_text()
    assert "# GritQL rule index" in text
    assert "Total rules: **2**" in text
    assert "## Layer: `entity`" in text
    assert "## Layer: `controller`" in text
    assert "check_grit.sh" in text


def test_write_rules_md_empty_rules_list(grit_env):
    gri.write_rules_md([])
    text = grit_env["rules_md"].read_text()
    assert "Total rules: **0**" in text


# ── delete_rule ──────────────────────────────────────────────

def test_delete_rule_removes_block(grit_env):
    src = _write_grit_rule(
        grit_env["grit_patterns"], "multi.grit", rule_id="keep_me",
        extra_patterns=[{
            "id": "remove_me", "layer": "entity",
            "triggers": "*Entity.java", "severity": "error",
            "guide": "g", "summary": "s",
        }],
    )
    gri.write_rules_json(gri.parse_grit_rules())

    assert gri.delete_rule("remove_me") is True

    text = src.read_text()
    assert "pattern remove_me(" not in text
    assert "pattern keep_me(" in text


def test_delete_rule_unknown_returns_false(grit_env):
    gri.write_rules_json([])
    assert gri.delete_rule("nope") is False


def test_delete_rule_also_strips_from_disabled_list(grit_env):
    _write_grit_rule(grit_env["grit_patterns"], "r.grit",
                     rule_id="disabled_then_deleted")
    gri.write_rules_json(gri.parse_grit_rules())
    gri.set_rules_disabled(["disabled_then_deleted"], disabled=True)

    assert gri.delete_rule("disabled_then_deleted") is True
    assert "disabled_then_deleted" not in gri.load_disabled_rule_ids()


def test_delete_rule_removes_empty_source_file(grit_env):
    src = _write_grit_rule(grit_env["grit_patterns"], "only.grit",
                             rule_id="only_rule")
    gri.write_rules_json(gri.parse_grit_rules())
    assert gri.delete_rule("only_rule") is True
    assert not src.exists()


# ── remove_guide_rules ───────────────────────────────────────

def test_remove_guide_rules_drops_only_matching_guide(grit_env, monkeypatch):
    """Deleting a pattern must remove exactly the rules it imported
    (guide == slug) from the grit_dir + index, leaving other guides alone."""
    monkeypatch.setattr(gri, "_undeployed_guides", lambda: set())
    mine = _write_grit_rule(grit_env["grit_patterns"], "mine.grit",
                            rule_id="mine_rule", guide="my-pattern")
    other = _write_grit_rule(grit_env["grit_patterns"], "other.grit",
                             rule_id="other_rule", guide="keep-pattern")
    gri.regenerate(write_guides=False)

    out = gri.remove_guide_rules("my-pattern")

    assert out == {"removed": 1, "rule_ids": ["mine_rule"]}
    assert not mine.exists()        # sole rule → file removed
    assert other.exists()           # other guide untouched
    ids = {r["id"] for r in gri.load_rules_index()["rules"]}
    assert ids == {"other_rule"}    # index regenerated without the deleted rule


def test_remove_guide_rules_no_match_is_noop(grit_env, monkeypatch):
    monkeypatch.setattr(gri, "_undeployed_guides", lambda: set())
    _write_grit_rule(grit_env["grit_patterns"], "r.grit", guide="other")
    gri.regenerate(write_guides=False)

    assert gri.remove_guide_rules("absent") == {"removed": 0, "rule_ids": []}
    assert len(gri.load_rules_index()["rules"]) == 1


# ── update_rule ──────────────────────────────────────────────

def test_update_rule_changes_metadata(grit_env):
    src = _write_grit_rule(grit_env["grit_patterns"], "r.grit",
                             rule_id="edit_me", severity="error",
                             summary="old summary")
    gri.write_rules_json(gri.parse_grit_rules())

    out = gri.update_rule("edit_me",
                            {"severity": "warn", "summary": "new"})
    assert out is True

    text = src.read_text()
    assert "// @rule severity=warn" in text
    assert "// @rule summary=new" in text
    # GritQL body untouched.
    assert "pattern edit_me()" in text


def test_update_rule_full_source_replacement(grit_env):
    src = _write_grit_rule(grit_env["grit_patterns"], "r.grit",
                             rule_id="replace_me")
    gri.write_rules_json(gri.parse_grit_rules())

    new_source = (
        "// @rule id=replace_me\n"
        "// @rule layer=entity\n"
        "// @rule triggers=*.java\n"
        "// @rule severity=info\n"
        "// @rule guide=g\n"
        "// @rule summary=brand new\n"
        "pattern replace_me() { interface_declaration(name=$n) }"
    )
    assert gri.update_rule("replace_me", {"source": new_source}) is True
    text = src.read_text()
    assert "interface_declaration" in text
    assert "class_declaration" not in text


def test_update_rule_unknown_returns_false(grit_env):
    gri.write_rules_json([])
    assert gri.update_rule("nope", {"summary": "x"}) is False


# ── _strip_verification_section (single file) ───────────────

def test_strip_verification_section_removes_block(grit_env):
    guide = grit_env["patterns"] / "g" / "SKILL.md"
    guide.parent.mkdir()
    guide.write_text(
        "## Disciplines\n\n- do X\n\n"
        "## Verification\n\n- run grit\n\n"
        "## Anti-Patterns\n\n- don't\n"
    )
    assert gri._strip_verification_section(str(guide)) is True
    text = guide.read_text()
    assert "## Verification" not in text
    assert "## Disciplines" in text
    assert "## Anti-Patterns" in text


def test_strip_verification_section_no_change_returns_false(grit_env):
    guide = grit_env["patterns"] / "g" / "SKILL.md"
    guide.parent.mkdir()
    guide.write_text("## Disciplines\n\n- do X\n")
    assert gri._strip_verification_section(str(guide)) is False


# ── _strip_verification_sections (bulk) ─────────────────────

def test_strip_verification_sections_counts_and_skips_dotdirs(grit_env):
    # Guide with a Verification block.
    (grit_env["patterns"] / "a").mkdir()
    (grit_env["patterns"] / "a" / "SKILL.md").write_text(
        "## H\n\nx\n\n## Verification\n\nv\n"
    )
    # Guide without.
    (grit_env["patterns"] / "b").mkdir()
    (grit_env["patterns"] / "b" / "SKILL.md").write_text("## H\n\ny\n")
    # Hidden dir — skipped.
    (grit_env["patterns"] / ".hidden").mkdir()
    (grit_env["patterns"] / ".hidden" / "SKILL.md").write_text(
        "## Verification\n\nz\n"
    )

    updated = gri._strip_verification_sections()
    assert updated == 1
    # Hidden SKILL.md untouched.
    assert "## Verification" in (
        grit_env["patterns"] / ".hidden" / "SKILL.md"
    ).read_text()


def test_strip_verification_sections_missing_patterns_dir_returns_zero(
        grit_env, monkeypatch):
    monkeypatch.setattr(settings, "patterns_dir",
                        str(grit_env["root"] / "nonexistent"))
    assert gri._strip_verification_sections() == 0


# ── regenerate ──────────────────────────────────────────────

def test_regenerate_produces_json_and_md(grit_env, monkeypatch):
    _write_grit_rule(grit_env["grit_patterns"], "r.grit")
    # Stub out _undeployed_guides to avoid hitting the real skill registry.
    monkeypatch.setattr(gri, "_undeployed_guides", lambda: set())

    out = gri.regenerate(write_guides=False)
    assert out["rules"] == 1
    assert os.path.isfile(grit_env["rules_json"])
    assert os.path.isfile(grit_env["rules_md"])

    data = json.loads(grit_env["rules_json"].read_text())
    assert data["rules"][0]["id"] == "example_rule"
    # disabled flag populated.
    assert "disabled" in data["rules"][0]


def test_regenerate_marks_disabled_and_undeployed(grit_env, monkeypatch):
    _write_grit_rule(grit_env["grit_patterns"], "a.grit",
                     rule_id="explicit_disable", guide="g1")
    _write_grit_rule(grit_env["grit_patterns"], "b.grit",
                     rule_id="undeployed_guide", guide="g2")
    gri.set_rules_disabled(["explicit_disable"], disabled=True)
    monkeypatch.setattr(gri, "_undeployed_guides", lambda: {"g2"})

    gri.regenerate(write_guides=False)
    data = json.loads(grit_env["rules_json"].read_text())
    by_id = {r["id"]: r for r in data["rules"]}
    assert by_id["explicit_disable"]["disabled"] is True
    assert by_id["undeployed_guide"]["disabled"] is True
