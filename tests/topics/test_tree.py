"""Unit tests for the topic-graph navigation tree (lib/topics/tree.py).

Pure functions over a graph dict — no DB, no I/O. Covers root/child
assembly, subtree collection (incl. cycle safety), and the blurb fallback.
"""

from lib.topics.tree import blurb_of, build_tree, node_card, subtree_ids


def _graph():
    return {
        "topics": {
            "root-a": {"label": "Root A", "blurb": "bucket A", "parent_id": None},
            "root-b": {"label": "Root B", "intent": "x" * 200, "parent_id": None},
            "leaf-1": {"label": "Leaf 1", "parent_id": "root-a",
                       "refs": [{"path": "a.py", "role": "impl"}]},
            "leaf-2": {"label": "Leaf 2", "parent_id": "root-a"},
            "mid": {"label": "Mid", "parent_id": "root-b"},
            "deep": {"label": "Deep", "parent_id": "mid"},
            "orphan": {"label": "Orphan", "parent_id": "ghost"},
        }
    }


def test_build_tree_roots_and_children():
    tree = build_tree(_graph())
    # explicit roots + the dangling-parent orphan, all sorted
    assert tree["roots"] == ["orphan", "root-a", "root-b"]
    assert tree["children"]["root-a"] == ["leaf-1", "leaf-2"]
    assert tree["children"]["root-b"] == ["mid"]
    assert tree["children"]["mid"] == ["deep"]


def test_subtree_ids_collects_descendants():
    ids = subtree_ids(_graph(), "root-b")
    assert set(ids) == {"root-b", "mid", "deep"}
    # a leaf returns just itself; an unknown id returns nothing
    assert subtree_ids(_graph(), "leaf-1") == ["leaf-1"]
    assert subtree_ids(_graph(), "nope") == []


def test_subtree_ids_cycle_safe():
    g = {"topics": {
        "a": {"parent_id": "b"}, "b": {"parent_id": "a"},
    }}
    # a<->b cycle must terminate and not duplicate
    out = subtree_ids(g, "a")
    assert sorted(out) == ["a", "b"]


def test_blurb_falls_back_to_truncated_intent():
    g = _graph()["topics"]
    assert blurb_of(g["root-a"]) == "bucket A"      # authored blurb wins
    assert blurb_of(g["root-b"]) == "x" * 120        # intent truncated to 120
    assert blurb_of(g["leaf-2"]) == ""               # neither → empty


def test_node_card_counts():
    g = _graph()
    card = node_card(g, "root-a", mem_count=3)
    assert card["child_count"] == 2
    assert card["mem_count"] == 3
    assert node_card(g, "leaf-1")["ref_count"] == 1
    assert node_card(g, "missing") is None
