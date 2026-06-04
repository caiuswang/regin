"""Unit tests for lib.search.

Covers tag/category filtering and Python-side text matching. Uses
tmp_db so each test starts with an empty pattern_docs + tags state,
then seeds rows via SQLModel directly.
"""

from __future__ import annotations

import os

from lib.search import _extract_snippet, search_patterns


# ── _extract_snippet (pure) ───────────────────────────────────

def test_extract_snippet_hits_middle_of_string():
    content = "A" * 200 + "NEEDLE" + "B" * 200
    snippet = _extract_snippet(content, "NEEDLE", context_chars=50)
    assert "NEEDLE" in snippet
    assert snippet.startswith("...")
    assert snippet.endswith("...")


def test_extract_snippet_no_match_returns_empty():
    assert _extract_snippet("hay stack", "missing") == ""


def test_extract_snippet_hit_at_start_has_no_leading_ellipsis():
    snippet = _extract_snippet("NEEDLE at start", "NEEDLE")
    assert not snippet.startswith("...")


def test_extract_snippet_hit_at_end_has_no_trailing_ellipsis():
    snippet = _extract_snippet("NEEDLE", "NEEDLE")
    assert not snippet.endswith("...")


def test_extract_snippet_is_case_insensitive():
    snippet = _extract_snippet("Find the NeEdLe here", "needle")
    assert "NeEdLe" in snippet


# ── search_patterns (DB-backed) ──────────────────────────────

def _seed_pattern(session, slug, title, category="procedure"):
    from lib.orm.models import PatternDoc
    doc = PatternDoc(
        slug=slug, title=title, file_path=f"{slug}/SKILL.md",
        category=category,
    )
    session.add(doc)
    session.flush()
    return doc.id


def _seed_tag(session, name, category="concept"):
    from lib.orm.models import Tag
    t = Tag(name=name, category=category)
    session.add(t)
    session.flush()
    return t.id


def _link_doc_tag(session, doc_id, tag_id):
    from lib.orm.models import DocTag
    session.add(DocTag(doc_id=doc_id, tag_id=tag_id))


def test_search_empty_db(tmp_db):
    assert search_patterns("anything") == []


def test_search_title_match_case_insensitive(tmp_db):
    from lib.orm import SessionLocal
    with SessionLocal() as s:
        _seed_pattern(s, "rest-controller", "REST Controller Conventions")
        s.commit()
    results = search_patterns("REST")
    assert len(results) == 1
    assert results[0]["slug"] == "rest-controller"
    # The same search is case-insensitive thanks to .lower() in Python path.
    assert search_patterns("rest")[0]["slug"] == "rest-controller"


def test_search_slug_match(tmp_db):
    from lib.orm import SessionLocal
    with SessionLocal() as s:
        _seed_pattern(s, "caching-pattern", "Spring Cache with Redis")
        s.commit()
    # The user types the slug; it's in the searchable string too.
    results = search_patterns("caching-pattern")
    assert len(results) == 1


def test_search_non_matching_query_returns_empty(tmp_db):
    from lib.orm import SessionLocal
    with SessionLocal() as s:
        _seed_pattern(s, "x", "Some Title")
        s.commit()
    # query string appears nowhere in title/slug/repo/tags/file content.
    assert search_patterns("completely-unrelated-query") == []


def test_search_tag_filter(tmp_db):
    from lib.orm import SessionLocal
    with SessionLocal() as s:
        a = _seed_pattern(s, "a", "Alpha")
        b = _seed_pattern(s, "b", "Beta")
        t = _seed_tag(s, "layer-tag", "layer")
        _link_doc_tag(s, a, t)
        s.commit()
    results = search_patterns("", tag="layer-tag")
    assert {r["slug"] for r in results} == {"a"}


def test_search_category_filter(tmp_db):
    from lib.orm import SessionLocal
    with SessionLocal() as s:
        _seed_pattern(s, "p1", "Proc", category="procedure")
        _seed_pattern(s, "m1", "Manual", category="manual")
        s.commit()
    results = search_patterns("", category="manual")
    assert {r["slug"] for r in results} == {"m1"}


def test_search_includes_tags_in_dict(tmp_db):
    from lib.orm import SessionLocal
    with SessionLocal() as s:
        a = _seed_pattern(s, "a", "Alpha")
        t1 = _seed_tag(s, "tag-one")
        t2 = _seed_tag(s, "tag-two")
        _link_doc_tag(s, a, t1)
        _link_doc_tag(s, a, t2)
        s.commit()
    results = search_patterns("")  # empty query returns everything
    assert len(results) == 1
    assert sorted(results[0]["tags"]) == ["tag-one", "tag-two"]


def test_search_file_content_match_sets_snippet(tmp_db, tmp_path, monkeypatch):
    """A query that matches only the file body (not title/slug/tags)
    still returns the row, with a `snippet` key around the match."""
    from lib.orm import SessionLocal
    from lib.settings import settings
    with SessionLocal() as s:
        _seed_pattern(s, "doc-slug", "Plain Title")
        s.commit()
    # The pattern's file lives at <patterns_dir>/<slug>/SKILL.md.
    monkeypatch.setattr(settings, "patterns_dir", tmp_path)
    doc_file = tmp_path / "doc-slug" / "SKILL.md"
    doc_file.parent.mkdir(parents=True)
    doc_file.write_text("intro paragraph mentioning ZZUNIQUEZZ in the body")

    # Query appears only in the file body, not title or slug.
    results = search_patterns("ZZUNIQUEZZ")
    assert len(results) == 1
    assert results[0]["slug"] == "doc-slug"
    assert "ZZUNIQUEZZ" in results[0]["snippet"]


def test_search_no_snippet_key_when_matched_via_title(tmp_db):
    """Rows that match via title/slug (no file read) carry no snippet key."""
    from lib.orm import SessionLocal
    with SessionLocal() as s:
        _seed_pattern(s, "rest-controller", "REST Controller Conventions")
        s.commit()
    results = search_patterns("REST")
    assert len(results) == 1
    assert "snippet" not in results[0]
