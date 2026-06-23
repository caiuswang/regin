"""Tests for the global meta-roots overlay (lib/topics/meta_roots.py).

The overlay adds cross-repo `skills` / `preferences` bucket roots to the
navigation surface without touching a repo's `topic.json`. These cover:
the bundled file loads as buckets, the merge surfaces them through the same
`build_tree` the index walks, a repo topic wins on id collision, a
missing/empty meta file is a no-op, and the full file→link→browse round-trip
(a `preference` memory filed under `preferences` shows up in that subtree).
"""

from lib.topics.meta_roots import load_global_meta_topics, merge_meta_roots
from lib.topics.tree import build_tree, is_bucket, subtree_ids


def _repo_graph():
    return {
        "version": 1, "repo": "regin",
        "topics": {
            "session-trace": {"label": "Session trace", "kind": "bucket",
                              "parent_id": None},
            "merge": {"label": "Merge", "parent_id": "session-trace"},
        },
    }


def test_bundled_meta_loads_with_skill_and_preference_buckets():
    meta = load_global_meta_topics()
    assert {"skills", "preferences"} <= set(meta)
    assert is_bucket(meta["skills"]) and is_bucket(meta["preferences"])
    # seed leaves hang under their buckets
    assert meta["pref-workflow"]["parent_id"] == "preferences"
    assert meta["skill-goal-verified"]["parent_id"] == "skills"


def test_merge_surfaces_meta_roots_alongside_repo_roots():
    merged = merge_meta_roots(_repo_graph())
    roots = build_tree(merged)["roots"]
    assert {"session-trace", "skills", "preferences"} <= set(roots)
    # the meta buckets actually carry their seed children
    assert "pref-workflow" in build_tree(merged)["children"]["preferences"]
    assert "skill-goal-verified" in build_tree(merged)["children"]["skills"]


def test_repo_topic_wins_on_id_collision():
    g = _repo_graph()
    g["topics"]["skills"] = {"label": "Repo's own skills topic",
                             "kind": "bucket", "parent_id": None}
    merged = merge_meta_roots(g)
    assert merged["topics"]["skills"]["label"] == "Repo's own skills topic"


def test_merge_does_not_mutate_input():
    g = _repo_graph()
    before = set(g["topics"])
    merge_meta_roots(g)
    assert set(g["topics"]) == before  # original untouched


def test_missing_meta_file_is_noop(monkeypatch):
    import lib.topics.meta_roots as mr
    monkeypatch.setattr(mr, "load_global_meta_topics", lambda: {})
    g = _repo_graph()
    assert merge_meta_roots(g)["topics"] == g["topics"]


def test_unreadable_meta_file_returns_empty(monkeypatch, tmp_path):
    import lib.topics.meta_roots as mr
    bad = tmp_path / "meta_roots.json"
    bad.write_text("{ not valid json")
    monkeypatch.setattr(mr, "_META_PATH", bad)
    assert mr.load_global_meta_topics() == {}


def test_file_link_browse_roundtrip():
    """A preference memory filed under `preferences` is found by the same
    subtree query the index_fetch browse leg uses."""
    import lib.memory as memory
    store = memory.get_store()
    mid = memory.remember("Always use the .venv interpreter, never bare python",
                          kind="preference", scope="global",
                          title="venv interpreter", is_test=True)
    store.link_authoritative_topic(mid, "preferences", source="manual")

    merged = merge_meta_roots(_repo_graph())
    ids = store.memories_for_topic_subtree(
        subtree_ids(merged, "preferences"), scope=None)
    assert mid in ids
