"""Unit tests for the hybrid retrieval helpers introduced in the
dense-search refactor. Covers the pure-Python bits that don't need
torch / transformers loaded: RRF fusion, FTS5 MATCH escaping, FTS
trigger sync, and the description-slot builder.
"""

from __future__ import annotations

import sqlite3

import pytest

from lib.patterns import pattern_router
from lib.orm import SessionLocal
from lib.orm.models import PatternDoc


# ── RRF ─────────────────────────────────────────────────────────

def test_rrf_dominant_when_both_legs_agree():
    """Same pid ranked #1 by both legs → it wins by a clear margin."""
    fused = pattern_router._rrf([[10, 20, 30], [10, 40, 50]], k=60)
    assert fused[0][0] == 10
    # 10 appears in both at rank 1 → 1/61 + 1/61
    assert fused[0][1] == pytest.approx(2 / 61)


def test_rrf_fills_gaps_from_only_one_leg():
    """A pid that only one leg ranked still appears, ranked behind anything
    both legs agreed on."""
    fused = pattern_router._rrf([[10, 20], [30, 10]], k=60)
    slugs = [pid for pid, _ in fused]
    assert slugs[0] == 10  # consensus
    assert set(slugs) == {10, 20, 30}


def test_rrf_empty_legs_returns_empty():
    assert pattern_router._rrf([], k=60) == []
    assert pattern_router._rrf([[], []], k=60) == []


# ── FTS5 MATCH escaping ─────────────────────────────────────────

def test_fts_query_keeps_alnum_tokens():
    assert pattern_router._fts_query("gritql java") == '"gritql" OR "java"'


def test_fts_query_drops_punctuation_safely():
    # `regin-bundle/v1` would otherwise raise fts5: syntax error
    out = pattern_router._fts_query("regin-bundle/v1")
    assert out == '"regin" OR "bundle" OR "v1"'


def test_fts_query_empty_when_no_tokens():
    assert pattern_router._fts_query("!!!") == ""
    assert pattern_router._fts_query("") == ""


# ── Description slot ─────────────────────────────────────────────

def test_description_slot_includes_category_description_and_tags():
    pd = PatternDoc(slug="x", title="X", file_path="x.md",
                    category="procedure")
    slot = pattern_router._description_slot(pd, "frontmatter desc", ["a", "b"])
    assert slot == "procedure | frontmatter desc | tags: a, b"


def test_description_slot_omits_empty_pieces():
    pd = PatternDoc(slug="x", title="X", file_path="x.md",
                    category="procedure")
    assert pattern_router._description_slot(pd, "", []) == "procedure"
    assert pattern_router._description_slot(pd, "d", []) == "procedure | d"
    assert pattern_router._description_slot(pd, "", ["a"]) == "procedure | tags: a"


# ── FTS5 trigger ────────────────────────────────────────────────

def test_fts_delete_trigger_cascades(tmp_db):
    """Deleting a pattern_doc cascades to patterns_fts via the trigger."""
    raw = sqlite3.connect(str(tmp_db))
    try:
        raw.execute(
            "INSERT INTO pattern_docs(slug, title, file_path, category) "
            "VALUES ('demo', 'Demo', 'demo.md', 'procedure')"
        )
        raw.execute(
            "INSERT INTO patterns_fts(slug, title, description, category, "
            "tag_names, body) VALUES ('demo', 'Demo', 'desc', 'procedure', "
            "'tag1', 'body text')"
        )
        raw.commit()
        cnt = raw.execute(
            "SELECT count(*) FROM patterns_fts WHERE slug = 'demo'"
        ).fetchone()[0]
        assert cnt == 1
        raw.execute("DELETE FROM pattern_docs WHERE slug = 'demo'")
        raw.commit()
        cnt_after = raw.execute(
            "SELECT count(*) FROM patterns_fts WHERE slug = 'demo'"
        ).fetchone()[0]
        assert cnt_after == 0
    finally:
        raw.close()


# ── _upsert_fts replaces, not appends ───────────────────────────

def test_upsert_fts_is_idempotent(tmp_db):
    """Calling _upsert_fts twice for the same slug leaves exactly one row."""
    with SessionLocal() as session:
        pd = PatternDoc(slug="up", title="Up", file_path="up.md",
                        category="procedure")
        session.add(pd)
        session.commit()
        session.refresh(pd)

        pattern_router._upsert_fts(session, pd, "first desc", "body1", ["t1"])
        pattern_router._upsert_fts(session, pd, "second desc", "body2", ["t2"])
        session.commit()

    raw = sqlite3.connect(str(tmp_db))
    try:
        rows = raw.execute(
            "SELECT description, body, tag_names FROM patterns_fts WHERE slug = 'up'"
        ).fetchall()
    finally:
        raw.close()

    assert len(rows) == 1
    assert rows[0] == ("second desc", "body2", "t2")


# ── Unified routing: memory mapping + merge + composition ───────

def test_route_snippet_first_nonempty_line_capped():
    assert pattern_router._route_snippet("\n\n  hello world  \nsecond") == "hello world"
    long = "x" * 100
    out = pattern_router._route_snippet(long, cap=10)
    assert out == "x" * 9 + "…"
    assert pattern_router._route_snippet("") == ""


def test_memory_header_formats_kind_scope_and_signals():
    h = pattern_router._memory_header({
        "kind": "gotcha", "scope": "repo:regin",
        "recall_count": 3, "importance": 0.85,
    })
    assert h == "Memory: gotcha | scope:repo:regin | recalled 3× | importance 0.85"
    # zero recall_count is omitted; missing fields degrade gracefully
    assert pattern_router._memory_header({}) == "Memory: lesson | scope:global"


def test_route_unified_separates_guidance_from_memories(monkeypatch):
    """Procedures (pattern/wiki) and memories live in distinct sections — a
    high-scoring memory never displaces a lower-scoring procedure, because
    they are not in the same ranked list at all."""
    def fake_route(query, *, kinds=None, **kw):
        return [{"slug": "pat", "source_kind": "pattern", "score": 0.4,
                 "score_kind": "rerank"}]

    def fake_mem(query, top_k, repo):
        return [{"slug": "memory/x", "source_kind": "memory", "score": 0.9,
                 "score_kind": "rerank"}]

    monkeypatch.setattr(pattern_router, "route", fake_route)
    monkeypatch.setattr(pattern_router, "_memory_route_results", fake_mem)

    out = pattern_router.route_unified("q", top_k=5)
    assert [r["slug"] for r in out["guidance"]] == ["pat"]
    assert [r["slug"] for r in out["memories"]] == ["memory/x"]


def test_route_unified_kinds_select_legs(monkeypatch):
    """`kinds` decides which legs run: pattern/wiki → route(), memory →
    _memory_route_results. Neither leg is invoked for a source not requested."""
    calls = {}

    def fake_route(query, *, kinds=None, **kw):
        calls["route_kinds"] = kinds
        return [{"slug": "pat", "source_kind": "pattern", "score": 0.4,
                 "score_kind": "rerank"}]

    def fake_mem(query, top_k, repo):
        calls["mem"] = True
        return [{"slug": "memory/x", "source_kind": "memory", "score": 0.7,
                 "score_kind": "rerank"}]

    monkeypatch.setattr(pattern_router, "route", fake_route)
    monkeypatch.setattr(pattern_router, "_memory_route_results", fake_mem)

    # default: both legs, route scoped to pattern+wiki
    calls.clear()
    out = pattern_router.route_unified("q", top_k=5)
    assert calls["route_kinds"] == ["pattern", "wiki"] and calls.get("mem")
    assert [r["slug"] for r in out["guidance"]] == ["pat"]
    assert [r["slug"] for r in out["memories"]] == ["memory/x"]

    # memory-only: route() not called, guidance empty
    calls.clear()
    out = pattern_router.route_unified("q", kinds=["memory"])
    assert "route_kinds" not in calls and calls.get("mem")
    assert out["guidance"] == []
    assert [r["slug"] for r in out["memories"]] == ["memory/x"]

    # pattern-only: memory leg not called, memories empty
    calls.clear()
    out = pattern_router.route_unified("q", kinds=["pattern"])
    assert calls["route_kinds"] == ["pattern"] and "mem" not in calls
    assert [r["slug"] for r in out["guidance"]] == ["pat"]
    assert out["memories"] == []
