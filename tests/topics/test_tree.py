"""Unit tests for the topic-graph navigation tree (lib/topics/tree.py) and
the taxonomy-placement validation/proposal plumbing.

Pure functions over a graph dict — no DB, no I/O. Covers bucket-aware
root/child assembly, the `unclassified` safety valve, subtree collection
(incl. cycle safety), the blurb fallback, the unclassified validation
warning, and parent_id surviving the proposal→approved shape.
"""

from lib.topics.diff import _approved_shape
from lib.topics.proposals.topic_actions import _approved_topic_from_proposal
from lib.topics.tree import (
    UNCLASSIFIED, blurb_of, build_tree, is_bucket, node_card, subtree_ids,
)
from lib.topics.validation import audit_graph


def _bucket(label, **extra):
    return {"label": label, "kind": "bucket", "parent_id": None, **extra}


def _graph():
    return {
        "version": 1, "repo": "test",
        "topics": {
            "root-a": _bucket("Root A", blurb="bucket A"),
            "root-b": _bucket("Root B", intent="x" * 200),
            UNCLASSIFIED: _bucket("Unclassified"),
            "leaf-1": {"label": "Leaf 1", "parent_id": "root-a",
                       "refs": [{"path": "a.py", "role": "impl"}]},
            "leaf-2": {"label": "Leaf 2", "parent_id": "root-a"},
            "mid": {"label": "Mid", "parent_id": "root-b"},
            "deep": {"label": "Deep", "parent_id": "mid"},
            "orphan": {"label": "Orphan", "parent_id": "ghost"},
            "rootless": {"label": "Rootless"},  # no parent_id at all
        }
    }


def test_buckets_are_roots_orphans_quarantined():
    tree = build_tree(_graph())
    # roots = bucket ids only (sorted); unclassified shown because it has orphans
    assert tree["roots"] == ["root-a", "root-b", UNCLASSIFIED]
    assert tree["children"]["root-a"] == ["leaf-1", "leaf-2"]
    assert tree["children"]["root-b"] == ["mid"]
    # a multi-level chain that REACHES a bucket nests: "deep"→"mid"→"root-b",
    # so "deep" hangs under "mid", not flattened to the top level
    assert tree["children"]["mid"] == ["deep"]
    # only a dangling parent ("orphan"→"ghost") and a missing parent_id
    # ("rootless") route to unclassified — never silently promoted
    assert tree["children"][UNCLASSIFIED] == ["orphan", "rootless"]


def test_unclassified_hidden_when_empty():
    g = _graph()
    for tid in ("orphan", "rootless", "deep", "mid"):
        g["topics"].pop(tid)
    tree = build_tree(g)
    assert UNCLASSIFIED not in tree["roots"]
    assert tree["roots"] == ["root-a", "root-b"]


def test_unclassified_surfaced_when_bucket_undeclared():
    # graph never declares the reserved `unclassified` bucket, yet has orphans:
    # the quarantine root is still surfaced so the leaves aren't dropped
    g = _graph()
    g["topics"].pop(UNCLASSIFIED)
    tree = build_tree(g)
    assert UNCLASSIFIED in tree["roots"]
    # "deep" nests under "mid" (chain reaches root-b), so only the truly
    # unrooted leaves are quarantined
    assert tree["children"][UNCLASSIFIED] == ["orphan", "rootless"]


def test_is_bucket():
    g = _graph()["topics"]
    assert is_bucket(g["root-a"]) and not is_bucket(g["leaf-1"])


def test_subtree_ids():
    # a bucket subtree = the bucket plus its leaves
    assert sorted(subtree_ids(_graph(), "root-a")) == ["leaf-1", "leaf-2", "root-a"]
    assert subtree_ids(_graph(), "leaf-1") == ["leaf-1"]  # leaf → just itself
    assert subtree_ids(_graph(), "nope") == []            # unknown → empty
    # a multi-level subtree carries its nested descendants: root-b → mid → deep
    assert sorted(subtree_ids(_graph(), "root-b")) == ["deep", "mid", "root-b"]
    assert sorted(subtree_ids(_graph(), "mid")) == ["deep", "mid"]


def test_nested_leaf_under_unrooted_leaf():
    # the real bug: a sub-topic ("child") whose parent ("parent") is itself
    # unrooted. The child nests under its parent (not flattened as a sibling),
    # the parent subtree carries it, and only the unrooted parent is flagged.
    g = {"topics": {
        UNCLASSIFIED: _bucket("Unclassified"),
        "parent": {"label": "Parent"},                       # no parent_id
        "child": {"label": "Child", "parent_id": "parent"},  # nests under parent
    }}
    tree = build_tree(g)
    assert tree["children"][UNCLASSIFIED] == ["parent"]
    assert tree["children"]["parent"] == ["child"]
    assert sorted(subtree_ids(g, "parent")) == ["child", "parent"]
    flagged = {i.topic_ids[0] for i in audit_graph(g)
               if i.code == "topic.unclassified"}
    assert flagged == {"parent"}        # child is classified under parent


def test_effective_parent_non_bucket_cycle_quarantined():
    # a parent_id cycle among non-bucket leaves can't reach a bucket; both
    # members route to unclassified instead of vanishing from the tree
    g = {"topics": {
        UNCLASSIFIED: _bucket("Unclassified"),
        "a": {"label": "A", "parent_id": "b"},
        "b": {"label": "B", "parent_id": "a"},
    }}
    tree = build_tree(g)
    assert tree["children"][UNCLASSIFIED] == ["a", "b"]


def test_subtree_ids_cycle_safe():
    # bucket-only parenting can't form a children-map cycle, but the
    # depth-first walk still carries a seen-guard against a malformed graph
    cyc = {"topics": {
        "a": {"kind": "bucket", "parent_id": "b"},
        "b": {"kind": "bucket", "parent_id": "a"},
    }}
    assert subtree_ids(cyc, "a") == ["a"]  # terminates, no infinite loop


def test_blurb_falls_back_to_truncated_intent():
    g = _graph()["topics"]
    assert blurb_of(g["root-a"]) == "bucket A"
    assert blurb_of(g["root-b"]) == "x" * 120
    assert blurb_of(g["leaf-2"]) == ""


def test_node_card_counts():
    g = _graph()
    card = node_card(g, "root-a", mem_count=3)
    assert card["child_count"] == 2 and card["mem_count"] == 3
    assert node_card(g, "leaf-1")["ref_count"] == 1
    assert node_card(g, "missing") is None


def test_audit_warns_on_unclassified_leaf():
    codes = audit_graph(_graph())
    unclassified = [i for i in codes if i.code == "topic.unclassified"]
    flagged = {i.topic_ids[0] for i in unclassified}
    # only the unrooted leaves warn; properly-bucketed leaves do not, and
    # "deep" is classified transitively (deep→mid→root-b) so it is NOT flagged
    assert {"orphan", "rootless"} <= flagged
    assert "leaf-1" not in flagged
    assert "deep" not in flagged
    assert all(i.severity == "warning" for i in unclassified)


def test_proposal_shape_preserves_parent_id_and_blurb():
    prop = {"id": "t", "label": "T", "intent": "i",
            "parent_id": "root-a", "blurb": "drill here"}
    for shape in (_approved_shape(prop), _approved_topic_from_proposal(prop)):
        assert shape["parent_id"] == "root-a"
        assert shape["blurb"] == "drill here"
    # a topic with no parent_id yields null → routes to unclassified, no crash
    assert _approved_shape({"id": "u", "label": "U", "intent": "i"})[
        "parent_id"] is None
