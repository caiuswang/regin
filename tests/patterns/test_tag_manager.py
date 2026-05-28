"""Unit tests for lib.tags.tag_manager.

Covers both the DB surface (ensure_tags_exist, list_tags, add_tag) and
the YAML-rule-driven auto-tagging helpers (auto_tag_doc, auto_tag_domain,
_tags_from_annotations, _tags_from_metadata_patterns, _tags_from_repo).

The rule globals (LAYER_AUTO_TAGS, ANNOTATION_TAGS, etc.) are module-
level snapshots loaded once at import; tests monkeypatch them to seed
deterministic fixtures without touching the user's real YAML.
"""

from __future__ import annotations

import pytest

from lib.settings import settings
from lib.tags import tag_manager
from lib.tags.tag_manager import add_tag, ensure_tags_exist, list_tags


# ── rule-globals fixture ─────────────────────────────────────

@pytest.fixture
def seeded_rules(monkeypatch):
    """Install a minimal deterministic rule set for auto-tag tests."""
    monkeypatch.setattr(tag_manager, "LAYER_AUTO_TAGS", {
        "entity": ["entity", "orm"],
        "controller": ["rest"],
        "repository": ["orm"],
    })
    monkeypatch.setattr(tag_manager, "ANNOTATION_TAGS", {
        "@RemoteService": ["remote"],
        "@RestController": ["rest"],
    })
    monkeypatch.setattr(tag_manager, "REPO_DOMAIN_TAGS", {
        r"^example-service$": ["example"],
        r"^billing-.*": ["billing"],
    })
    monkeypatch.setattr(tag_manager, "_METADATA_PATTERNS", {
        "extends": {"BaseEntity": ["base-class"]},
        "imports": {"com/billing": ["billing-client"]},
    })
    monkeypatch.setattr(tag_manager, "_LAYER_COMBINATIONS", [
        {"any_of": ["entity", "repository"], "tags": ["persistence"]},
    ])
    yield


def test_ensure_tags_exist_creates_missing(tmp_db):
    ensure_tags_exist(["brand-new-one", "brand-new-two"])
    names = {t["name"] for t in list_tags()}
    assert "brand-new-one" in names
    assert "brand-new-two" in names


def test_ensure_tags_exist_is_idempotent(tmp_db):
    ensure_tags_exist(["same"])
    ensure_tags_exist(["same"])
    count = sum(1 for t in list_tags() if t["name"] == "same")
    assert count == 1


def test_ensure_tags_exist_assigns_concept_category_by_default(tmp_db):
    ensure_tags_exist(["unknown-shape"])
    rows = list_tags()
    found = next((t for t in rows if t["name"] == "unknown-shape"), None)
    assert found is not None
    assert found["category"] == "concept"


def test_list_tags_filter_by_category(tmp_db):
    add_tag("layer-one", "layer", "first layer tag")
    add_tag("domain-one", "domain", "first domain tag")
    layers = list_tags(category="layer")
    names = {t["name"] for t in layers}
    assert "layer-one" in names
    assert "domain-one" not in names


def test_list_tags_includes_doc_count_column(tmp_db):
    add_tag("solo", "concept")
    rows = list_tags()
    solo = next(t for t in rows if t["name"] == "solo")
    assert solo["doc_count"] == 0  # no doc_tags links yet


def test_add_tag_is_idempotent(tmp_db):
    add_tag("t", "concept")
    add_tag("t", "concept")  # second call no-ops
    names = [t["name"] for t in list_tags() if t["name"] == "t"]
    assert names == ["t"]


def test_ensure_tags_exist_empty_list_is_noop(tmp_db):
    before = {t["name"] for t in list_tags()}
    ensure_tags_exist([])
    after = {t["name"] for t in list_tags()}
    assert before == after


def test_add_tag_accepts_optional_description(tmp_db):
    add_tag("labeled", "concept", description="a useful note")
    row = next(t for t in list_tags() if t["name"] == "labeled")
    assert row["description"] == "a useful note"


# ── _load_rules ──────────────────────────────────────────────

def test_load_rules_missing_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "auto_tag_rules_path",
                        str(tmp_path / "nope.yaml"))
    assert tag_manager._load_rules() == {}


def test_load_rules_reads_yaml_file(monkeypatch, tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("layers:\n  entity: [e]\n")
    monkeypatch.setattr(settings, "auto_tag_rules_path", str(f))
    assert tag_manager._load_rules() == {"layers": {"entity": ["e"]}}


def test_load_rules_empty_file_returns_empty_dict(monkeypatch, tmp_path):
    f = tmp_path / "empty.yaml"
    f.write_text("")
    monkeypatch.setattr(settings, "auto_tag_rules_path", str(f))
    assert tag_manager._load_rules() == {}


# ── _tags_from_annotations ───────────────────────────────────

def test_tags_from_annotations_matches(seeded_rules):
    out = tag_manager._tags_from_annotations(["@RemoteService", "@Other"])
    assert out == {"remote"}


def test_tags_from_annotations_empty_iter():
    assert tag_manager._tags_from_annotations(None) == set()
    assert tag_manager._tags_from_annotations([]) == set()


# ── _tags_from_metadata_patterns ─────────────────────────────

def test_tags_from_metadata_patterns_string_value(seeded_rules):
    md = {"extends": "com.example.BaseEntity"}
    assert tag_manager._tags_from_metadata_patterns(md) == {"base-class"}


def test_tags_from_metadata_patterns_iterable_value(seeded_rules):
    md = {"imports": ["com/billing/foo", "com/other/bar"]}
    assert tag_manager._tags_from_metadata_patterns(md) == {"billing-client"}


def test_tags_from_metadata_patterns_no_match(seeded_rules):
    md = {"extends": "Unrelated"}
    assert tag_manager._tags_from_metadata_patterns(md) == set()


def test_tags_from_metadata_patterns_missing_field(seeded_rules):
    assert tag_manager._tags_from_metadata_patterns({}) == set()


# ── _tags_from_repo ──────────────────────────────────────────

def test_tags_from_repo_exact_regex(seeded_rules):
    assert tag_manager._tags_from_repo("example-service") == {"example"}


def test_tags_from_repo_prefix_regex(seeded_rules):
    assert tag_manager._tags_from_repo("billing-core") == {"billing"}


def test_tags_from_repo_unknown_repo_empty(seeded_rules):
    assert tag_manager._tags_from_repo("unrelated-repo") == set()


# ── auto_tag_doc ─────────────────────────────────────────────

def test_auto_tag_doc_combines_layer_annotations_metadata_repo(seeded_rules):
    md = {
        "annotations": ["@RemoteService"],
        "extends": "BaseEntity",
    }
    out = tag_manager.auto_tag_doc("entity", md, "example-service")
    assert set(out) == {
        "entity", "orm", "remote", "base-class", "example",
    }
    # Sorted for determinism.
    assert out == sorted(out)


def test_auto_tag_doc_unknown_category_returns_only_matchers(seeded_rules):
    out = tag_manager.auto_tag_doc("unknown-layer", {}, "unrelated-repo")
    assert out == []


# ── auto_tag_domain ───────────────────────────────────────────

def test_auto_tag_domain_layer_union(seeded_rules):
    layers = {
        "entity": {"path": "E.java", "content": ""},
        "controller": {"path": "C.java", "content": ""},
    }
    out = tag_manager.auto_tag_domain(layers, "unrelated")
    # entity → [entity, orm]; controller → [rest];
    # layer_combinations: entity present → [persistence]
    assert set(out) == {"entity", "orm", "rest", "persistence"}


def test_auto_tag_domain_annotation_found_in_content(seeded_rules):
    layers = {
        "controller": {
            "path": "C.java",
            "content": "package x;\n@RestController\nclass C {}",
        },
    }
    out = tag_manager.auto_tag_domain(layers, "unrelated")
    # controller layer → [rest]; @RestController annotation → [rest];
    # union is still {rest}.
    assert "rest" in out


def test_auto_tag_domain_repo_tags_applied(seeded_rules):
    out = tag_manager.auto_tag_domain({}, "example-service")
    assert "example" in out


def test_auto_tag_domain_layer_combination_any_of(seeded_rules):
    # 'repository' alone should still trigger the combination tag.
    out = tag_manager.auto_tag_domain(
        {"repository": {"path": "R.java", "content": ""}},
        "unrelated",
    )
    assert "persistence" in out
    assert "orm" in out


def test_auto_tag_domain_empty_inputs_produce_empty(seeded_rules):
    assert tag_manager.auto_tag_domain({}, "unrelated") == []


def test_auto_tag_domain_non_dict_layer_value_tolerated(seeded_rules):
    # A layer whose value isn't a dict (e.g. None) shouldn't crash.
    layers = {"entity": None}
    out = tag_manager.auto_tag_domain(layers, "unrelated")
    assert "entity" in out
