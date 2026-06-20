"""Query-log term weighting: the repo-adaptive second layer on the wordfreq
prior. Covers the bounded `repo_factor` multiplier, the cache round-trip, and
the end-to-end effect on `_fuzzy_best` scoring — a word that saturates the
repo's prompt log loses to a rarer one even when both hit.
"""

from __future__ import annotations

import json

from lib.settings import settings
from lib.topics.route import _fuzzy_best
from lib.topics.term_weights import (
    _cache_path,
    load_query_df,
    rebuild_query_df,
    repo_factor,
)


# --- repo_factor: the bounded multiplier ---------------------------------

def test_repo_factor_inert_below_min_queries():
    # A sparse log must not distort routing: factor is a flat 1.0.
    n = settings.agent_memory.topic_route_querylog_min_queries - 1
    assert repo_factor("memory", n, {"memory": n}) == 1.0


def test_repo_factor_unseen_word_is_unpenalised():
    n = settings.agent_memory.topic_route_querylog_min_queries + 100
    assert repo_factor("exemplar", n, {}) == 1.0


def test_repo_factor_shrinks_saturating_word_to_floor_band():
    n = settings.agent_memory.topic_route_querylog_min_queries + 50
    floor = settings.agent_memory.topic_route_querylog_floor
    ubiquitous = repo_factor("memory", n, {"memory": n - 5})  # in ~all prompts
    rare = repo_factor("exemplar", n, {"exemplar": 1})        # in 1 prompt
    assert floor <= ubiquitous < rare <= 1.0
    assert ubiquitous <= floor + 0.2   # close to the floor


# --- cache round-trip ----------------------------------------------------

def test_rebuild_writes_cache_and_load_reads_it(fake_git_repo, monkeypatch):
    rows = ["fix the memory recall", "polish the memory page", "trace spans"]

    def _fake_queries():
        return rows

    monkeypatch.setattr("lib.topics.term_weights._routed_queries", _fake_queries)
    count = rebuild_query_df(fake_git_repo)
    assert count == 3

    path = _cache_path(fake_git_repo)
    assert path.is_file()
    payload = json.loads(path.read_text())
    assert payload["n"] == 3
    assert payload["df"]["memory"] == 2   # two of the three prompts
    assert payload["df"]["recall"] == 1

    n, df = load_query_df(fake_git_repo)
    assert n == 3 and df["memory"] == 2


def test_load_missing_cache_is_noop(fake_git_repo):
    n, df = load_query_df(fake_git_repo)
    assert (n, df) == (0, {})


# --- end-to-end: scoring actually changes --------------------------------

def _topic(label: str) -> dict:
    return {"label": label, "aliases": [], "intent": "", "status": "active",
            "refs": [], "edges": [], "commands": [],
            "include_globs": [], "exclude_globs": []}


def test_querylog_breaks_a_tie_pure_wordfreq_would_lose():
    # Two topics, each hit by exactly two of the query's keywords. Pure
    # wordfreq (n=0) leans on raw informativeness; the query log, having seen
    # `memory` in nearly every prompt, demotes the memory-heavy topic.
    topics = {
        "memory-heavy": _topic("memory recall surface"),
        "trace-side": _topic("recall trace spans"),
    }
    query = "memory recall trace"
    n = settings.agent_memory.topic_route_querylog_min_queries + 50
    df = {"memory": n - 2, "recall": n // 2, "trace": 3}
    weighted_best, _ = _fuzzy_best(query, topics, n, df)
    assert weighted_best == "trace-side"
    # Without the query log both still route, but the memory topic is no
    # longer penalised — the factor only applies when n/df are supplied.
    plain_best, _ = _fuzzy_best(query, topics)
    assert plain_best in topics   # routes somewhere; tie no longer broken away
