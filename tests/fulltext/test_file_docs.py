"""The ripgrep-backed kinds, and the Python fallback that stands in for rg."""

from __future__ import annotations

import pytest

from lib.fulltext import grep
from lib.fulltext.file_docs import (
    _heading_anchor, _title_of, search_file_docs, search_pattern_docs,
)


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """A patterns tree with one real page and one vendored copy."""
    root = tmp_path / "patterns"
    (root / "guides").mkdir(parents=True)
    (root / "guides" / "limits.md").write_text(
        "## Rate limiting\n\nA token bucket.\n\n## Redis store\n\nUses redis Lua.\n"
    )
    vendor = root / ".venv" / "lib" / "site-packages" / "thing"
    vendor.mkdir(parents=True)
    (vendor / "README.md").write_text("redis redis redis redis redis\n")
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, "patterns_dir", root)
    monkeypatch.setattr(settings_mod.settings, "skills_dir", tmp_path / "absent")
    monkeypatch.setattr(settings_mod.settings, "repo_paths", [])
    return root


@pytest.fixture(params=["ripgrep", "fallback"])
def either_backend(request, monkeypatch):
    """Every assertion below must hold whether or not rg is installed."""
    if request.param == "fallback":
        monkeypatch.setattr(grep, "ripgrep_path", lambda: None)
    elif not grep.ripgrep_path():
        pytest.skip("ripgrep not installed")
    return request.param


def test_title_uses_the_first_heading_at_any_level():
    assert _title_of("## Session trace design\n\nbody", "fb") == "Session trace design"
    assert _title_of("# Top\n", "fb") == "Top"
    assert _title_of("no heading here", "fb") == "fb"


def test_heading_anchor_names_the_section_above_the_hit():
    text = "## Rate limiting\n\nbucket\n\n## Redis store\n\nuses redis\n"
    assert _heading_anchor(text, "redis") == "redis-store"


def test_heading_anchor_is_empty_when_the_hit_precedes_every_heading():
    assert _heading_anchor("redis\n\n## Later\n", "redis") == ""


def test_pattern_search_finds_a_page_and_anchors_its_href(corpus, either_backend):
    (hit,) = [h for h in search_pattern_docs("token bucket", 6)]
    assert hit.doc.title == "Rate limiting"
    assert hit.doc.href.startswith("/patterns/guides/limits")


def test_pattern_search_skips_vendored_trees(corpus, either_backend):
    hits = search_pattern_docs("redis", 6)
    assert [h.doc.href for h in hits] == ["/patterns/guides/limits#redis-store"]


def test_pattern_search_ranks_a_title_hit_above_a_body_hit(corpus, either_backend):
    (root,) = [corpus]
    (root / "guides" / "redis.md").write_text("## Redis\n\nnothing else\n")
    hits = search_pattern_docs("redis", 6)
    assert hits[0].doc.title == "Redis"


def test_file_search_bodies_come_from_disk_not_from_the_diff(tmp_path, either_backend):
    target = tmp_path / "gate.py"
    target.write_text("def gate():\n    return rateLimit\n")
    hits = search_file_docs(
        [{"path": str(target), "span_id": "sp1", "trace_id": "t1",
          "additions": 3, "deletions": 1}],
        "rateLimit", 6)
    assert hits[0].doc.body == "def gate():\n    return rateLimit\n"
    assert hits[0].doc.tag == "+3 −1"
    assert hits[0].doc.href == "/trace/sessions/t1?span=sp1"
    assert hits[0].doc.mono is True
    assert hits[0].doc.as_json()["source"] == {"session": "t1", "path": str(target)}


def test_file_search_skips_a_path_that_no_longer_exists(tmp_path, either_backend):
    assert search_file_docs(
        [{"path": str(tmp_path / "gone.py"), "span_id": "s", "trace_id": "t"}],
        "anything", 6) == []


def test_file_row_without_a_span_gets_no_href(tmp_path, either_backend):
    target = tmp_path / "x.txt"
    target.write_text("redis\n")
    (hit,) = search_file_docs([{"path": str(target)}], "redis", 6)
    assert hit.doc.href == ""
    assert "source" not in hit.doc.as_json()


def test_count_matches_agrees_across_both_backends(tmp_path, monkeypatch):
    if not grep.ripgrep_path():
        pytest.skip("ripgrep not installed")
    (tmp_path / "a.md").write_text("redis\nREDIS\nother\n")
    (tmp_path / "b.md").write_text("nothing\n")
    args = ([str(tmp_path)], "redis")
    kwargs = {"globs": ["*.md"], "suffixes": (".md",)}
    with_rg = grep.count_matches(*args, **kwargs)
    monkeypatch.setattr(grep, "ripgrep_path", lambda: None)
    without_rg = grep.count_matches(*args, **kwargs)
    assert with_rg == without_rg == {str(tmp_path / "a.md"): 2}


def test_a_broken_ripgrep_falls_back_instead_of_reporting_an_empty_corpus(
        tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("redis\n")
    monkeypatch.setattr(grep, "ripgrep_path", lambda: "/nonexistent/rg")
    assert grep.count_matches([str(tmp_path)], "redis",
                              globs=["*.md"], suffixes=(".md",)) == {
        str(tmp_path / "a.md"): 1}


def test_the_same_guide_in_two_trees_yields_one_row(tmp_path, monkeypatch,
                                                    either_backend):
    """A pattern deployed into the skills tree is the same page twice. Its
    bytes differ (regin writes frontmatter into its own copy), so only an
    identity key collapses it."""
    patterns = tmp_path / "patterns" / "base-skill"
    skills = tmp_path / "skills" / "base-skill"
    patterns.mkdir(parents=True)
    skills.mkdir(parents=True)
    (patterns / "SKILL.md").write_text(
        "---\nmanual: true\n---\n# Base skill\n\nuses ripgrep\n")
    (skills / "SKILL.md").write_text("# Base skill\n\nuses ripgrep\n")
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, "patterns_dir", tmp_path / "patterns")
    monkeypatch.setattr(settings_mod.settings, "skills_dir", tmp_path / "skills")
    monkeypatch.setattr(settings_mod.settings, "repo_paths", [])
    hits = search_pattern_docs("ripgrep", 6)
    assert [h.doc.tag for h in hits] == ["pattern"], "the canonical root wins"


def test_two_genuinely_different_pages_both_survive(tmp_path, monkeypatch,
                                                    either_backend):
    root = tmp_path / "patterns"
    root.mkdir()
    (root / "a.md").write_text("# Alpha\n\nripgrep\n")
    (root / "b.md").write_text("# Beta\n\nripgrep\n")
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, "patterns_dir", root)
    monkeypatch.setattr(settings_mod.settings, "skills_dir", tmp_path / "absent")
    monkeypatch.setattr(settings_mod.settings, "repo_paths", [])
    assert len(search_pattern_docs("ripgrep", 6)) == 2


def _both_legs(tmp_path, monkeypatch, query):
    """count_matches under rg and under the fallback, for corpus parity."""
    args = ([str(tmp_path)], query)
    kwargs = {"globs": ["*.md"], "suffixes": (".md",)}
    with_rg = grep.count_matches(*args, **kwargs)
    monkeypatch.setattr(grep, "ripgrep_path", lambda: None)
    return with_rg, grep.count_matches(*args, **kwargs)


def test_both_legs_agree_when_a_gitignore_is_present(tmp_path, monkeypatch):
    """rg honours .gitignore during traversal and the Python walk does not, so
    without --no-ignore the two legs return different corpora."""
    if not grep.ripgrep_path():
        pytest.skip("ripgrep not installed")
    (tmp_path / ".gitignore").write_text("hidden.md\n")
    (tmp_path / "hidden.md").write_text("redis\n")
    (tmp_path / "shown.md").write_text("redis\n")
    with_rg, without_rg = _both_legs(tmp_path, monkeypatch, "redis")
    assert with_rg == without_rg
    assert str(tmp_path / "hidden.md") in with_rg


def test_both_legs_skip_a_binary_file(tmp_path, monkeypatch):
    if not grep.ripgrep_path():
        pytest.skip("ripgrep not installed")
    (tmp_path / "blob.md").write_bytes(b"redis\x00\x00binary tail redis\n")
    (tmp_path / "text.md").write_text("redis\n")
    with_rg, without_rg = _both_legs(tmp_path, monkeypatch, "redis")
    assert with_rg == without_rg == {str(tmp_path / "text.md"): 1}


def test_rules_are_reachable_by_id_and_by_summary(monkeypatch, tmp_path):
    """Rules live in the generated index, not on disk, so ripgrep cannot see
    them — losing them from the palette was a real regression when the old
    quicksearch endpoint went away."""
    from lib.fulltext import file_docs
    monkeypatch.setattr(file_docs, "load_rules_index", lambda: {"rules": [
        {"id": "py_bare_except", "guide": "regin-python-conventions",
         "summary": "Bare except swallows every exception", "severity": "warn"},
    ]})
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, "patterns_dir", tmp_path / "none")
    monkeypatch.setattr(settings_mod.settings, "skills_dir", tmp_path / "none2")
    monkeypatch.setattr(settings_mod.settings, "repo_paths", [])

    by_id = search_pattern_docs("py_bare_except", 6)
    assert by_id[0].doc.href == "/rules/py_bare_except"
    assert by_id[0].doc.tag == "rule"
    by_text = search_pattern_docs("swallows", 6)
    assert by_text[0].doc.title == "py_bare_except"


def test_a_rule_without_an_id_is_skipped(monkeypatch, tmp_path):
    from lib.fulltext import file_docs
    monkeypatch.setattr(file_docs, "load_rules_index",
                        lambda: {"rules": [{"summary": "redis"}]})
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, "patterns_dir", tmp_path / "none")
    monkeypatch.setattr(settings_mod.settings, "skills_dir", tmp_path / "none2")
    monkeypatch.setattr(settings_mod.settings, "repo_paths", [])
    assert search_pattern_docs("redis", 6) == []


def test_a_named_binary_file_is_dropped_by_both_legs(tmp_path, monkeypatch):
    """The `file` kind passes explicit paths, where rg would read up to the
    first NUL and report a partial count while the walk drops the file."""
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"redis\x00\x00tail\n")
    text = tmp_path / "ok.txt"
    text.write_text("redis\n")
    paths = [str(blob), str(text)]
    with_rg = grep.count_matches(paths, "redis", globs=[], suffixes=())
    monkeypatch.setattr(grep, "ripgrep_path", lambda: None)
    without_rg = grep.count_matches(paths, "redis", globs=[], suffixes=())
    assert with_rg == without_rg == {str(text): 1}
