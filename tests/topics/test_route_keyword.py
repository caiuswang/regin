"""Keyword routing: stopword-resistant fuzzy fallback + route explanation.

Covers the two fixes behind the topic-route playground's "why did it pick
this?" gap: (1) the fuzzy fallback ignores function words and demands ≥2
meaningful keyword hits, so prose no longer routes on `we/that/to`; (2)
`route_explain` reports the strategy and matched keywords that drove the route.
"""

from __future__ import annotations

from lib import topics
from lib.topics.route import (
    _fuzzy_best,
    _meaningful_keywords,
    match_topic,
    route_explain,
)


def _topic(label: str, *, intent: str = "", aliases=None, refs=None) -> dict:
    return {
        "label": label,
        "aliases": list(aliases or []),
        "intent": intent,
        "status": "active",
        "refs": [{"path": p} for p in (refs or [])],
        "edges": [],
        "commands": [],
        "include_globs": [],
        "exclude_globs": [],
    }


def _seed(repo, topics_map: dict) -> None:
    topics.bootstrap(repo)
    graph = topics.load_graph(repo)
    graph["topics"] = topics_map
    topics.save_graph(repo, graph)


# --- pure helpers (no graph needed) -------------------------------------

def test_meaningful_keywords_drops_stopwords_keeps_short_real_tokens():
    kws = _meaningful_keywords("we should fix the ui and db so it routes")
    assert "we" not in kws and "the" not in kws and "so" not in kws
    assert "ui" in kws and "db" in kws and "fix" in kws and "routes" in kws


def test_meaningful_keywords_dedups_in_order():
    assert _meaningful_keywords("apply apply views apply") == ["apply", "views"]


def test_fuzzy_best_requires_two_meaningful_hits():
    topics_map = {
        "proposal-pipeline": _topic("Topic proposal pipeline apply/stop"),
    }
    # Only "apply" overlaps — a single coincidental word must not route.
    best, hits = _fuzzy_best("apply the changes everywhere now", topics_map)
    assert best is None and hits == []


def test_fuzzy_best_routes_on_two_real_keywords():
    topics_map = {
        "recall": _topic("Memory recall pipeline rerank ranking"),
        "trace": _topic("Session trace timeline spans"),
    }
    best, hits = _fuzzy_best("fix the recall ranking bug", topics_map)
    assert best == "recall"
    assert set(hits) >= {"recall", "ranking"}


# --- end-to-end through the authoritative graph -------------------------

def test_prose_full_of_stopwords_routes_nowhere(fake_git_repo):
    _seed(fake_git_repo, {
        "proposal-pipeline": _topic(
            "Topic proposal pipeline (request -> draft -> review -> apply/stop)"),
    })
    # "apply" is the only meaningful overlap; the rest is function words.
    prose = ("currently we have some old views that do not apply those "
             "changes, so apply them and commit it")
    assert match_topic(fake_git_repo, prose) is None
    why = route_explain(fake_git_repo, prose)
    assert why == {"id": None, "strategy": None, "keywords": []}


def test_route_explain_reports_fuzzy_keywords(fake_git_repo):
    _seed(fake_git_repo, {
        "recall": _topic("Memory recall pipeline", intent="rerank ranking"),
    })
    why = route_explain(fake_git_repo, "fix the recall ranking rerank bug")
    assert why["id"] == "recall"
    assert why["strategy"] == "fuzzy keyword overlap"
    assert set(why["keywords"]) >= {"recall", "ranking", "rerank"}


def test_route_explain_precise_match_has_no_keyword_breakdown(fake_git_repo):
    _seed(fake_git_repo, {
        "recall": _topic("Memory recall pipeline",
                         aliases=["memory recall pipeline"]),
    })
    why = route_explain(fake_git_repo, "Memory recall pipeline")
    assert why["id"] == "recall"
    assert why["strategy"] == "alias / label (exact)"
    assert why["keywords"] == []
