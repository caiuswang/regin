"""topic.local.json overlay: merge semantics + write-routing invariants.

Covers the overlay primitives introduced when proposal approval / scan /
downgrade stopped writing the git-tracked base `topic.json` and started
writing the gitignored `topic.local.json` overlay instead. The effective
graph is `merge(base, overlay)`; all readers funnel through
`load_authoritative_graph`, which compares the merged disk against the
snapshot so the overlay is never invisible to the drift detector.
"""

from __future__ import annotations

import json

from lib.topics.apply import apply_diff
from lib.topics.core import (
    load_graph,
    load_graph_merged,
    load_local_graph,
    merge_graphs,
    save_graph,
    save_local_graph,
    topic_local_path,
    topic_path,
)
from lib.topics import (
    TopicGraphError,
    delete_topic,
    promote_all_topics,
    promote_topic,
)
from lib.topics.diff import diff_against_graph
from lib.topics.graph_io import _graph_hash, load_authoritative_graph
from lib.topics.snapshots import latest_snapshot, resolve_or_create_repo


def _topic(tid: str, **overrides) -> dict:
    base = {
        "label": tid.title(), "intent": f"{tid} intent", "status": "active",
        "aliases": [], "refs": [], "edges": [],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    base.update(overrides)
    return base


def _base(name: str = "demo", topics: dict | None = None) -> dict:
    return {"version": 1, "repo": name, "topics": topics or {}}


# ── merge_graphs unit semantics ─────────────────────────────────────────


def test_merge_absent_overlay_returns_base():
    base = _base(topics={"a": _topic("a")})
    assert merge_graphs(base, {"topics": {}, "deleted_topics": []}) == base


def test_merge_overlay_topic_fully_overrides_base():
    base = _base(topics={"a": _topic("a", aliases=["old"])})
    overlay = {"topics": {"a": _topic("a", aliases=["new"])}, "deleted_topics": []}
    merged = merge_graphs(base, overlay)
    assert merged["topics"]["a"]["aliases"] == ["new"]


def test_merge_overlay_adds_new_topic():
    base = _base(topics={"a": _topic("a")})
    overlay = {"topics": {"b": _topic("b")}, "deleted_topics": []}
    merged = merge_graphs(base, overlay)
    assert set(merged["topics"]) == {"a", "b"}


def test_merge_tombstone_drops_base_topic():
    base = _base(topics={"a": _topic("a"), "b": _topic("b")})
    overlay = {"topics": {}, "deleted_topics": ["a"]}
    merged = merge_graphs(base, overlay)
    assert set(merged["topics"]) == {"b"}


def test_merge_top_level_fields_come_from_base():
    base = _base(name="real-repo", topics={})
    base["version"] = 1
    overlay = {"repo": "ignored", "version": 99, "topics": {"x": _topic("x")}}
    merged = merge_graphs(base, overlay)
    assert merged["repo"] == "real-repo"
    assert merged["version"] == 1


# ── load_local_graph robustness ─────────────────────────────────────────


def test_load_local_graph_absent_returns_empty_shell(fake_git_repo):
    overlay = load_local_graph(fake_git_repo)
    assert overlay == {"topics": {}, "deleted_topics": []}


def test_save_then_load_local_graph_round_trips(fake_git_repo):
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    save_local_graph(fake_git_repo, {"topics": {"x": _topic("x")}, "deleted_topics": []})
    assert topic_local_path(fake_git_repo).exists()
    assert "x" in load_local_graph(fake_git_repo)["topics"]


# ── apply write-routing invariants ──────────────────────────────────────


def test_apply_leaves_base_untouched_and_writes_overlay(fake_git_repo):
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    base = _base()
    topic_path(fake_git_repo).write_text(json.dumps(base))
    before = topic_path(fake_git_repo).read_text()
    repo = resolve_or_create_repo(str(fake_git_repo))

    fresh = {"id": "svc", **_topic("svc")}
    apply_diff(repo.id, diff_against_graph(fresh, base, strategy="create"), reason="accept")

    # Base byte-identical; overlay carries the new topic.
    assert topic_path(fake_git_repo).read_text() == before
    assert "svc" not in load_graph(fake_git_repo)["topics"]
    assert "svc" in load_local_graph(fake_git_repo)["topics"]
    assert "svc" in load_graph_merged(fake_git_repo)["topics"]


def test_apply_keeps_merged_disk_hash_equal_to_snapshot(fake_git_repo):
    """The reconciliation invariant: merge(base, overlay) must hash-equal
    the stored snapshot, or the drift detector re-seeds and stomps it.
    """
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    base = _base()
    topic_path(fake_git_repo).write_text(json.dumps(base))
    repo = resolve_or_create_repo(str(fake_git_repo))

    fresh = {"id": "svc", **_topic("svc")}
    result = apply_diff(repo.id, diff_against_graph(fresh, base, strategy="create"), reason="accept")

    merged_hash = _graph_hash(load_graph_merged(fake_git_repo))
    snap_hash = _graph_hash(json.loads(result.snapshot.graph_json))
    assert merged_hash == snap_hash


def test_second_identical_apply_adds_no_auto_seed_snapshot(fake_git_repo):
    """A re-read after apply must not trip drift-detect into inserting an
    extra is_latest snapshot (the count stays 1 latest)."""
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    base = _base()
    topic_path(fake_git_repo).write_text(json.dumps(base))
    repo = resolve_or_create_repo(str(fake_git_repo))

    fresh = {"id": "svc", **_topic("svc")}
    apply_diff(repo.id, diff_against_graph(fresh, base, strategy="create"), reason="accept")

    # A read that would re-seed on drift; then confirm the latest graph
    # is unchanged and the merged disk still matches.
    merged_before = load_graph_merged(fake_git_repo)
    snap = latest_snapshot(repo.id)
    assert _graph_hash(merged_before) == _graph_hash(json.loads(snap.graph_json))


# ── deletion via tombstone (downgrade) ──────────────────────────────────


def test_overlay_tombstone_removes_base_topic_without_resurrection(fake_git_repo):
    """A base topic dropped by an apply lands as a tombstone; the merged
    graph omits it and a re-read does not bring it back."""
    from lib.topics.graph_io import export_overlay_to_disk

    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    base = _base(topics={"a": _topic("a"), "b": _topic("b")})
    topic_path(fake_git_repo).write_text(json.dumps(base))
    resolve_or_create_repo(str(fake_git_repo))

    # Prospective drops "a"; export must tombstone it in the overlay.
    prospective = _base(topics={"b": _topic("b")})
    export_overlay_to_disk(fake_git_repo, prospective)

    assert load_local_graph(fake_git_repo)["deleted_topics"] == ["a"]
    merged = load_graph_merged(fake_git_repo)
    assert "a" not in merged["topics"]
    assert "b" in merged["topics"]
    # Base file still carries both — only the overlay tombstone hides "a".
    assert set(load_graph(fake_git_repo)["topics"]) == {"a", "b"}


# ── promote_topic (overlay → base) ──────────────────────────────────────


def test_promote_moves_overlay_topic_into_base_and_clears_overlay(fake_git_repo):
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    topic_path(fake_git_repo).write_text(json.dumps(_base(topics={"a": _topic("a")})))
    save_local_graph(fake_git_repo, {"topics": {"b": _topic("b")}, "deleted_topics": []})

    before = load_graph_merged(fake_git_repo)
    result = promote_topic(fake_git_repo, "b")

    assert result == {"topic_id": "b", "action": "added"}
    # b now lives in the git-tracked base; overlay no longer carries it.
    assert "b" in load_graph(fake_git_repo)["topics"]
    assert "b" not in load_local_graph(fake_git_repo)["topics"]
    # The effective graph is unchanged by promotion.
    assert load_graph_merged(fake_git_repo) == before


def test_promote_tombstone_removes_topic_from_base(fake_git_repo):
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    topic_path(fake_git_repo).write_text(json.dumps(_base(topics={"a": _topic("a"), "b": _topic("b")})))
    save_local_graph(fake_git_repo, {"topics": {}, "deleted_topics": ["a"]})

    before = load_graph_merged(fake_git_repo)
    result = promote_topic(fake_git_repo, "a")

    assert result == {"topic_id": "a", "action": "removed"}
    assert "a" not in load_graph(fake_git_repo)["topics"]
    assert load_local_graph(fake_git_repo)["deleted_topics"] == []
    assert load_graph_merged(fake_git_repo) == before


def test_promote_unknown_topic_raises(fake_git_repo):
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    topic_path(fake_git_repo).write_text(json.dumps(_base()))
    save_local_graph(fake_git_repo, {"topics": {}, "deleted_topics": []})

    try:
        promote_topic(fake_git_repo, "nope")
    except TopicGraphError as exc:
        assert "nothing to promote" in str(exc)
    else:
        raise AssertionError("expected TopicGraphError for a topic not in the overlay")


def test_promote_bootstraps_base_when_absent(fake_git_repo):
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    # No base topic.json — only an overlay entry.
    save_local_graph(fake_git_repo, {"topics": {"x": _topic("x")}, "deleted_topics": []})

    promote_topic(fake_git_repo, "x")

    assert topic_path(fake_git_repo).exists()
    assert "x" in load_graph(fake_git_repo)["topics"]


# ── promote_all_topics (whole overlay → base) ───────────────────────────


def test_promote_all_moves_adds_and_removes_in_one_pass(fake_git_repo):
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    topic_path(fake_git_repo).write_text(
        json.dumps(_base(topics={"a": _topic("a"), "old": _topic("old")}))
    )
    save_local_graph(
        fake_git_repo,
        {"topics": {"b": _topic("b"), "c": _topic("c")}, "deleted_topics": ["old"]},
    )

    before = load_graph_merged(fake_git_repo)
    result = promote_all_topics(fake_git_repo)

    assert result == {"added": ["b", "c"], "removed": ["old"]}
    base = load_graph(fake_git_repo)["topics"]
    assert set(base) == {"a", "b", "c"}
    overlay = load_local_graph(fake_git_repo)
    assert overlay["topics"] == {} and overlay["deleted_topics"] == []
    # The effective graph is unchanged by promotion.
    assert load_graph_merged(fake_git_repo) == before


def test_promote_all_is_noop_on_empty_overlay(fake_git_repo):
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    topic_path(fake_git_repo).write_text(json.dumps(_base(topics={"a": _topic("a")})))
    save_local_graph(fake_git_repo, {"topics": {}, "deleted_topics": []})

    assert promote_all_topics(fake_git_repo) == {"added": [], "removed": []}
    assert set(load_graph(fake_git_repo)["topics"]) == {"a"}
    assert "x" not in load_local_graph(fake_git_repo)["topics"]


# ── delete_topic (hard delete) ──────────────────────────────────────────


def test_delete_removes_base_topic_and_wiki(fake_git_repo):
    wiki_dir = fake_git_repo / ".regin" / "topics" / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    topic_path(fake_git_repo).write_text(json.dumps(_base(topics={"a": _topic("a"), "b": _topic("b")})))
    (wiki_dir / "a.md").write_text("# A\n\nnarrative\n")
    resolve_or_create_repo(str(fake_git_repo))
    load_authoritative_graph(fake_git_repo)  # seed snapshot

    result = delete_topic(fake_git_repo, "a")

    assert result["topic_id"] == "a"
    assert result["wiki_removed"] is True
    assert "a" not in load_graph(fake_git_repo)["topics"]
    assert "a" not in load_graph_merged(fake_git_repo)["topics"]
    assert not (wiki_dir / "a.md").exists()
    from lib.topics.graph_io import check_graph_sync
    assert check_graph_sync(str(fake_git_repo))["state"] == "in_sync"
    # The wiki index was regenerated and no longer links the deleted topic.
    index = (wiki_dir / "index.md").read_text()
    assert "(a.md)" not in index
    assert "(b.md)" in index


def test_delete_prunes_inbound_edges(fake_git_repo):
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    base = _base(topics={
        "a": _topic("a"),
        "b": _topic("b", edges=[{"target": "a", "type": "related"}]),
    })
    topic_path(fake_git_repo).write_text(json.dumps(base))
    resolve_or_create_repo(str(fake_git_repo))
    load_authoritative_graph(fake_git_repo)

    result = delete_topic(fake_git_repo, "a")

    assert result["pruned_edges"] == 1
    assert load_graph_merged(fake_git_repo)["topics"]["b"]["edges"] == []


def test_delete_overlay_only_topic_leaves_base_untouched(fake_git_repo):
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    topic_path(fake_git_repo).write_text(json.dumps(_base(topics={"a": _topic("a")})))
    save_local_graph(fake_git_repo, {"topics": {"b": _topic("b")}, "deleted_topics": []})
    resolve_or_create_repo(str(fake_git_repo))
    load_authoritative_graph(fake_git_repo)
    base_before = topic_path(fake_git_repo).read_text()

    delete_topic(fake_git_repo, "b")

    assert "b" not in load_graph_merged(fake_git_repo)["topics"]
    assert "a" in load_graph(fake_git_repo)["topics"]
    # base file byte-untouched: an overlay-only delete never rewrites it.
    assert topic_path(fake_git_repo).read_text() == base_before


def test_delete_unknown_topic_raises(fake_git_repo):
    (fake_git_repo / ".regin" / "topics").mkdir(parents=True, exist_ok=True)
    topic_path(fake_git_repo).write_text(json.dumps(_base(topics={"a": _topic("a")})))
    resolve_or_create_repo(str(fake_git_repo))
    load_authoritative_graph(fake_git_repo)

    try:
        delete_topic(fake_git_repo, "nope")
    except TopicGraphError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected TopicGraphError for an unknown topic")
