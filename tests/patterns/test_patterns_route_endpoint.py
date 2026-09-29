"""Characterization of `/api/patterns/route` (dense pattern routing):
arg parsing, dependency failure, empty index, and doc/tag/score assembly."""

from __future__ import annotations

import pytest

from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDoc, Tag


@pytest.fixture
def router(monkeypatch):
    from lib.patterns import pattern_router
    state = {"hits": [], "calls": [], "raise": None}

    def fake_route(query, *, top_k, rerank):
        state["calls"].append((query, top_k, rerank))
        if state["raise"]:
            raise state["raise"]
        return state["hits"]
    monkeypatch.setattr(pattern_router, "route", fake_route)
    return state


def _seed(slug, tags=()):
    with SessionLocal() as session:
        doc = PatternDoc(slug=slug, title=slug.upper(),
                         file_path=f"patterns/{slug}/SKILL.md",
                         category="procedure", content_hash="0" * 64)
        session.add(doc)
        session.flush()
        for name in tags:
            tag = Tag(name=name, category="concept")
            session.add(tag)
            session.flush()
            session.add(DocTag(doc_id=doc.id, tag_id=tag.id))
        session.commit()


def _get(client, qs):
    resp = client.get(f"/api/patterns/route{qs}")
    return resp.status_code, resp.get_json()


def test_route_requires_q(flask_client, tmp_db, router):
    assert _get(flask_client, "?q=%20") == (400, {"error": "missing q"})
    assert router["calls"] == []


@pytest.mark.parametrize("qs,call", [
    ("?q=x", ("x", 10, True)),
    ("?q=x&top_k=3&rerank=NO", ("x", 3, False)),
    ("?q=x&top_k=abc&rerank=0", ("x", 10, False)),
    ("?q=%20x%20&rerank=yes", ("x", 10, True)),
])
def test_route_args_and_empty_index(flask_client, tmp_db, router, qs, call):
    assert _get(flask_client, qs) == (200, {
        "query": call[0], "docs": [], "score_kind": None,
        "hint": "no patterns indexed — run `regin pattern embed`",
    })
    assert router["calls"] == [call]


def test_route_dependency_error(flask_client, tmp_db, router):
    from lib.skills import skill_router
    router["raise"] = skill_router.DependencyError("no torch")
    assert _get(flask_client, "?q=x") == (503, {
        "error": "router dependencies missing", "detail": "no torch",
        "install_hint": "pip install -r requirements-router.txt",
    })


def test_route_assembles_docs_in_route_order(flask_client, tmp_db, router):
    _seed("alpha", tags=("t1", "t2"))
    _seed("beta")
    router["hits"] = [
        {"slug": "beta", "score": 0.9, "score_kind": "rerank"},
        {"slug": "ghost", "score": 0.5, "score_kind": "rerank"},
        {"slug": "alpha", "score": 0.4, "score_kind": "rerank"},
    ]
    status, body = _get(flask_client, "?q=find")
    assert status == 200
    assert (body["query"], body["score_kind"]) == ("find", "rerank")
    got = [(d["slug"], d["score"], d["tag_names"]) for d in body["docs"]]
    assert got == [("beta", 0.9, ""), ("alpha", 0.4, "t1, t2")]
