"""Tests for the split-leaf orchestrator: pure plan assembly, the agentic
proposer's parse/fail-loud contract, and gated end-to-end apply.

`tmp_db` + `tmp_memory_db` (autouse) isolate both DBs, so `remember()` and the
relink writes never touch the real stores.
"""

from __future__ import annotations

import json

import pytest

import lib.memory as memory
from lib.topics.split_leaf import (
    ClusterProposerUnavailable, SplitCluster, apply_split, bucket_for_leaf,
    build_split_plan, propose_clusters,
)


class FakeLLM:
    def __init__(self, answer):
        self.answer = answer

    def complete(self, prompt, *, max_tokens=1024, surface_id=None):
        return self.answer


def _graph() -> dict:
    return {"version": 1, "repo": "test", "topics": {
        "b": {"label": "Bucket", "intent": "a bucket", "status": "active",
              "parent_id": None, "kind": "bucket", "aliases": [], "refs": [],
              "edges": [], "commands": [], "include_globs": ["lib/x/**"],
              "exclude_globs": []},
        "heavy": {"label": "Heavy", "intent": "heavy leaf", "status": "active",
                  "parent_id": "b", "aliases": [], "refs": [], "edges": [],
                  "commands": [], "include_globs": ["lib/x/**"], "exclude_globs": []},
    }}


# ── bucket resolution ───────────────────────────────────────────

def test_bucket_for_leaf():
    g = _graph()
    assert bucket_for_leaf(g, "heavy") == "b"
    assert bucket_for_leaf(g, "b") is None      # a bucket's parent is None
    assert bucket_for_leaf(g, "missing") is None


# ── pure plan assembly ──────────────────────────────────────────

def test_build_split_plan_mints_siblings_and_keeps_unplaced():
    g = _graph()
    links = {f"m{i}": "agent" for i in range(5)}
    clusters = [SplitCluster("Span Capture", "cap card", ["m0", "m1"]),
                SplitCluster("Merge Path", "merge card", ["m2", "m3"])]
    plan = build_split_plan("heavy", "b", clusters, links, g)

    assert set(plan.new_topics) == {"span-capture", "merge-path"}
    assert all(n["parent_id"] == "b" for n in plan.new_topics.values())
    assert plan.new_topics["span-capture"]["include_globs"] == ["lib/x/**"]
    assert plan.assignment["m0"] == "span-capture"
    assert plan.assignment["m4"] == "heavy"     # unplaced kept on the leaf


def test_build_split_plan_unique_ids_on_slug_collision():
    g = _graph()
    links = {"m0": "agent", "m1": "agent"}
    clusters = [SplitCluster("Merge!", "x", ["m0"]),
                SplitCluster("merge", "y", ["m1"])]
    plan = build_split_plan("heavy", "b", clusters, links, g)
    assert set(plan.new_topics) == {"merge", "merge-2"}


# ── proposer parse / fail-loud ──────────────────────────────────

def test_propose_clusters_parses_and_filters_unknown_ids():
    g = _graph()
    mems = [{"id": "m0", "title": "a", "body": "x"},
            {"id": "m1", "title": "b", "body": "y"}]
    answer = ('[{"label": "Cap", "intent": "cap card", '
              '"memory_ids": ["m0", "bogus"]}, '
              '{"label": "", "intent": "", "memory_ids": ["m1"]}]')
    clusters = propose_clusters(g["topics"]["heavy"], mems, FakeLLM(answer))
    assert len(clusters) == 1                      # empty-label cluster dropped
    assert clusters[0].label == "Cap"
    assert clusters[0].memory_ids == ["m0"]        # unknown id filtered out


def test_propose_clusters_fail_loud_without_llm():
    g = _graph()
    with pytest.raises(ClusterProposerUnavailable):
        propose_clusters(g["topics"]["heavy"],
                         [{"id": "m0", "title": "", "body": ""}], FakeLLM(None))


# ── gated apply (end-to-end) ────────────────────────────────────

def _seed_repo(tmp_path, graph) -> str:
    from lib.topics.core import write_split_graph
    write_split_graph(tmp_path, graph)
    return str(tmp_path)


def test_apply_split_end_to_end(tmp_path):
    g = _graph()
    repo = _seed_repo(tmp_path, g)
    store = memory.get_store()
    ids = [memory.remember(f"lesson body {i}", title=f"t{i}") for i in range(4)]
    for mid in ids:
        store.link_authoritative_topic(mid, "heavy", source="agent")
    links = {mid: "agent" for mid in ids}
    clusters = [SplitCluster("Span Capture", "cap", ids[:2]),
                SplitCluster("Merge Path", "merge", ids[2:])]
    plan = build_split_plan("heavy", "b", clusters, links, g)

    result = apply_split(store, repo, plan, g)

    from lib.topics.core import load_graph
    disk = load_graph(tmp_path)
    assert "span-capture" in disk["topics"]
    assert "merge-path" in disk["topics"]
    assert store.memories_for_topic_node("heavy") == []          # leaf shed all
    assert set(store.memories_for_topic_node("span-capture")) == set(ids[:2])
    assert result["moved"] == 4


def test_apply_split_refuses_ungated_plan(tmp_path):
    g = _graph()
    repo = _seed_repo(tmp_path, g)
    store = memory.get_store()
    mid = memory.remember("body", title="t")
    store.link_authoritative_topic(mid, "heavy", source="agent")
    plan = build_split_plan("heavy", "b", [SplitCluster("Sub", "s", [mid])],
                            {mid: "agent"}, g)
    plan.new_topics["sub"]["parent_id"] = "heavy"   # grandchild ⇒ gate fails

    with pytest.raises(ValueError, match="gate failed"):
        apply_split(store, repo, plan, g)
    assert mid in store.memories_for_topic_node("heavy")   # not mutated
