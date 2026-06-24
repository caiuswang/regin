"""Tests for the heavy-leaf split safety gate (prototype).

The core `check_split` is pure, so most tests pass a synthetic graph + a
`{memory_id: source}` map directly — no DB. One integration test exercises
`gather_leaf_links` against the autouse isolated store.
"""

from __future__ import annotations

import lib.memory as memory
from lib.topics.split_gate import (
    SplitPlan, check_split, gather_leaf_links,
)


def _leaf_node(parent: str) -> dict:
    return {"label": "Heavy leaf", "intent": "the heavy leaf", "status": "active",
            "parent_id": parent, "aliases": [], "refs": [], "edges": [],
            "commands": [], "include_globs": [], "exclude_globs": []}


def _new_node(parent: str, label: str) -> dict:
    return {"label": label, "intent": f"sub-topic {label}", "status": "active",
            "parent_id": parent, "aliases": [], "refs": [], "edges": [],
            "commands": [], "include_globs": [], "exclude_globs": []}


def _graph() -> dict:
    return {"version": 1, "repo": "test", "topics": {
        "b": {"label": "Bucket", "intent": "a bucket", "status": "active",
              "parent_id": None, "kind": "bucket", "aliases": [], "refs": [],
              "edges": [], "commands": [], "include_globs": [], "exclude_globs": []},
        "heavy": _leaf_node("b"),
    }}


def _good_plan() -> SplitPlan:
    return SplitPlan(
        leaf_id="heavy", bucket_id="b",
        new_topics={"sub-a": _new_node("b", "Sub A"),
                    "sub-b": _new_node("b", "Sub B")},
        assignment={**{f"m{i}": "sub-a" for i in range(10)},
                    **{f"m{i}": "sub-b" for i in range(10, 18)}})


def _links(plan: SplitPlan, source: str = "agent") -> dict[str, str]:
    return {m: source for m in plan.assignment}


# ── happy path ──────────────────────────────────────────────────

def test_good_split_passes_with_no_errors():
    plan = _good_plan()
    res = check_split(plan, _graph(), _links(plan))
    assert res.ok, res.errors
    assert res.errors == []
    assert res.stats["dest_counts"] == {"sub-a": 10, "sub-b": 8}


# ── gate 1: structural ──────────────────────────────────────────

def test_grandchild_parent_is_rejected():
    """The 2-level trap: a sub-topic parented to the leaf, not the bucket."""
    plan = _good_plan()
    plan.new_topics["sub-a"]["parent_id"] = "heavy"   # wrong — not a bucket
    res = check_split(plan, _graph(), _links(plan))
    assert not res.ok
    assert any("bucket sibling" in e or "unclassified" in e for e in res.errors)


def test_target_that_is_not_a_bucket_is_rejected():
    plan = _good_plan()
    plan.bucket_id = "heavy"        # a leaf, not kind:"bucket"
    for body in plan.new_topics.values():
        body["parent_id"] = "heavy"
    res = check_split(plan, _graph(), _links(plan))
    assert not res.ok
    assert any("kind:'bucket'" in e for e in res.errors)


# ── gate 2: conservation ────────────────────────────────────────

def test_missing_memory_trips_conservation():
    plan = _good_plan()
    links = _links(plan)
    links["orphan"] = "agent"       # on the leaf but absent from assignment
    res = check_split(plan, _graph(), links)
    assert not res.ok
    assert any("no destination" in e for e in res.errors)


def test_foreign_destination_trips_conservation():
    plan = _good_plan()
    plan.assignment["m0"] = "sub-z"   # not a split destination
    res = check_split(plan, _graph(), _links(plan))
    assert not res.ok
    assert any("not split destinations" in e for e in res.errors)


def test_keeping_some_on_the_leaf_is_allowed():
    plan = _good_plan()
    plan.assignment["m0"] = "heavy"   # kept as overview — a valid destination
    res = check_split(plan, _graph(), _links(plan))
    assert res.ok, res.errors


# ── gate 3: provenance ──────────────────────────────────────────

def test_moving_a_manual_link_is_blocked_by_default():
    plan = _good_plan()
    links = _links(plan)
    links["m0"] = "manual"           # human-pinned, plan moves it to sub-a
    res = check_split(plan, _graph(), links)
    assert not res.ok
    assert any("manual/reflect" in e for e in res.errors)


def test_protected_move_allowed_with_opt_in():
    plan = _good_plan()
    links = _links(plan)
    links["m0"] = "manual"
    res = check_split(plan, _graph(), links, allow_protected_move=True)
    assert res.ok, res.errors
    assert any("manual/reflect" in w for w in res.warnings)


def test_manual_link_kept_on_leaf_is_fine():
    plan = _good_plan()
    plan.assignment["m0"] = "heavy"   # not moved off the leaf
    links = _links(plan)
    links["m0"] = "manual"
    res = check_split(plan, _graph(), links)
    assert res.ok, res.errors


# ── soft gates (warn, don't block) ──────────────────────────────

def test_thin_leaf_and_tiny_subtopic_warn_but_pass():
    plan = SplitPlan(
        leaf_id="heavy", bucket_id="b",
        new_topics={"sub-a": _new_node("b", "Sub A"),
                    "sub-b": _new_node("b", "Sub B")},
        assignment={"m0": "sub-a", "m1": "sub-a", "m2": "sub-b"})  # 2 + 1, n=3
    res = check_split(plan, _graph(), _links(plan))
    assert res.ok, res.errors
    assert any("min_leaf" in w for w in res.warnings)
    assert any("min_per_topic" in w for w in res.warnings)


# ── live-store adapter ──────────────────────────────────────────

def test_gather_leaf_links_reads_sources_from_store():
    store = memory.get_store()
    m1 = memory.remember("first lesson body", title="one")
    m2 = memory.remember("second lesson body", title="two")
    store.link_authoritative_topic(m1, "heavy", source="agent")
    store.link_authoritative_topic(m2, "heavy", source="manual")
    links = gather_leaf_links(store, "heavy")
    assert links == {m1: "agent", m2: "manual"}
