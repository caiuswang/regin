"""`lib.memory.tree_io` — git-shareable markdown export/import of the
agent-memory store, mirrored onto the authoritative topic graph.

No `Repo` row is registered for the test's `tmp_path`, so
`load_authoritative_graph` falls straight to its disk-only fallback —
no git init, no `add-repo` needed, just a `.regin/topics/topic.json` on
disk (written via `lib.topics.core.save_graph`).
"""

from __future__ import annotations

from pathlib import Path

import lib.memory as memory
from lib.memory import tree_io
from lib.memory.tree_io import UNFILED_DIR, node_path, slugify
from lib.topics.core import save_graph
from lib.topics.tree import UNCLASSIFIED


def _bucket(label):
    return {"label": label, "kind": "bucket", "parent_id": None}


def _leaf(label, parent_id):
    return {"label": label, "parent_id": parent_id}


def _write_graph(repo_path: Path) -> dict:
    graph = {
        "version": 1, "repo": "t",
        "topics": {
            "alpha": _bucket("Alpha"),
            "beta": _bucket("Beta"),
            "leaf-a": _leaf("Leaf A", "alpha"),
            "leaf-b": _leaf("Leaf B", "beta"),
        },
    }
    save_graph(repo_path, graph)
    return graph


def _remember(body, **kw):
    kw.setdefault("title", body[:40])
    return memory.remember(body, **kw)


def _reset_memory_db(tmp_path, monkeypatch, name: str) -> None:
    """Point the memory engine at a fresh, empty DB file — simulates a
    teammate's machine that has never seen these memories."""
    from lib.settings import AgentMemoryConfig, settings
    from lib.memory.engine import dispose_memory_engine

    fresh = AgentMemoryConfig()
    fresh.db_path = tmp_path / name
    fresh.dense_enabled = False
    fresh.inject_dense_via_server = False
    monkeypatch.setattr(settings, "agent_memory", fresh)
    dispose_memory_engine()
    memory.reset_store()


# ── pure helpers ─────────────────────────────────────────────────────────


def test_slugify_basic():
    assert slugify("Restart the Backend!") == "restart-the-backend"
    assert slugify("  ---  ") == "untitled"
    assert slugify("A" * 100, max_len=10) == "a" * 10


def _graph_dict():
    return {"topics": {
        "alpha": _bucket("Alpha"),
        "beta": _bucket("Beta"),
        "leaf-a": _leaf("Leaf A", "alpha"),
        "deep": _leaf("Deep", "leaf-a"),
        "orphan": _leaf("Orphan", "ghost"),
    }}


def test_node_path_bucket_is_singleton():
    assert node_path(_graph_dict(), "alpha") == ["alpha"]


def test_node_path_nests_through_parents():
    assert node_path(_graph_dict(), "leaf-a") == ["alpha", "leaf-a"]
    assert node_path(_graph_dict(), "deep") == ["alpha", "leaf-a", "deep"]


def test_node_path_dangling_parent_falls_back_to_unclassified():
    assert node_path(_graph_dict(), "orphan") == [UNCLASSIFIED, "orphan"]


# ── export ───────────────────────────────────────────────────────────────


def test_export_orphan_lands_in_unfiled(tmp_path):
    _write_graph(tmp_path)
    _remember("an orphan lesson with no topic link", is_test=False)

    summary = tree_io.export_memory_tree(str(tmp_path))

    assert summary == {"canonical": 1, "stub": 0, "unfiled": 1}
    files = list((tmp_path / tree_io.DEFAULT_TREE_DIR / UNFILED_DIR).glob("*.md"))
    assert len(files) == 1


def test_export_single_link_writes_one_canonical_no_stub(tmp_path):
    _write_graph(tmp_path)
    mid = _remember("a lesson filed under leaf-a", is_test=False)
    memory.get_store().link_authoritative_topic(mid, "leaf-a")

    summary = tree_io.export_memory_tree(str(tmp_path))

    assert summary == {"canonical": 1, "stub": 0, "unfiled": 0}
    canon_dir = tmp_path / tree_io.DEFAULT_TREE_DIR / "alpha" / "leaf-a"
    files = list(canon_dir.glob("*.md"))
    assert len(files) == 1
    assert "a lesson filed under leaf-a" in files[0].read_text()


def test_export_multi_link_one_canonical_plus_stubs(tmp_path):
    """`leaf-a` < `leaf-b` lexicographically → leaf-a is canonical."""
    _write_graph(tmp_path)
    mid = _remember("a multi-filed lesson", is_test=False)
    store = memory.get_store()
    store.link_authoritative_topic(mid, "leaf-b")
    store.link_authoritative_topic(mid, "leaf-a")

    summary = tree_io.export_memory_tree(str(tmp_path))

    assert summary == {"canonical": 1, "stub": 1, "unfiled": 0}
    canon_files = list(
        (tmp_path / tree_io.DEFAULT_TREE_DIR / "alpha" / "leaf-a").glob("*.md"))
    stub_files = list(
        (tmp_path / tree_io.DEFAULT_TREE_DIR / "beta" / "leaf-b").glob("*.md"))
    assert len(canon_files) == 1 and len(stub_files) == 1
    assert canon_files[0].name == stub_files[0].name  # same relative filename
    # The stub body carries no lesson text — frontmatter only.
    stub_text = stub_files[0].read_text()
    assert "a multi-filed lesson" not in stub_text
    assert "canonical:" in stub_text


def test_export_deleted_topic_node_falls_back_to_unfiled(tmp_path):
    """Acceptance item 5: a linked node id absent from the current graph
    (topic deleted since the last export) must not crash."""
    _write_graph(tmp_path)
    mid = _remember("filed under a since-deleted topic", is_test=False)
    memory.get_store().link_authoritative_topic(mid, "no-longer-a-topic")

    summary = tree_io.export_memory_tree(str(tmp_path))

    assert summary == {"canonical": 1, "stub": 0, "unfiled": 1}


def test_export_counts_match_eligible_memories(tmp_path):
    """File counts: canonical == len(eligible memories); stub ==
    sum(max(0, len(links)-1))."""
    _write_graph(tmp_path)
    store = memory.get_store()
    m1 = _remember("lesson one", is_test=False)
    m2 = _remember("lesson two", is_test=False)
    m3 = _remember("lesson three, multi-filed", is_test=False)
    store.link_authoritative_topic(m1, "leaf-a")
    store.link_authoritative_topic(m2, "leaf-b")
    store.link_authoritative_topic(m3, "leaf-a")
    store.link_authoritative_topic(m3, "leaf-b")
    # A digest row must be excluded from export entirely (internal only).
    memory.remember("standing digest", kind="digest", is_test=False)
    # A test-only row must also be excluded.
    _remember("test-only lesson", is_test=True)

    summary = tree_io.export_memory_tree(str(tmp_path))

    assert summary["canonical"] == 3      # m1, m2, m3 — digest/test excluded
    assert summary["stub"] == 1           # only m3 has a 2nd link
    assert summary["unfiled"] == 0


def test_export_scope_filter(tmp_path):
    _write_graph(tmp_path)
    _remember("global-scope lesson", is_test=False, scope="global")
    _remember("repo-scope lesson", is_test=False, scope="repo:x")

    summary = tree_io.export_memory_tree(str(tmp_path), scope="repo:x")

    assert summary == {"canonical": 1, "stub": 0, "unfiled": 1}


def test_export_reexport_is_byte_identical(tmp_path):
    _write_graph(tmp_path)
    mid = _remember("stable lesson body", is_test=False)
    memory.get_store().link_authoritative_topic(mid, "leaf-a")

    tree_io.export_memory_tree(str(tmp_path))
    first = {p: p.read_bytes() for p in
             (tmp_path / tree_io.DEFAULT_TREE_DIR).rglob("*.md")}

    tree_io.export_memory_tree(str(tmp_path))
    second = {p: p.read_bytes() for p in
              (tmp_path / tree_io.DEFAULT_TREE_DIR).rglob("*.md")}

    assert first == second


# ── import / round-trip ────────────────────────────────────────────────


def test_import_from_empty_dir_is_a_noop(tmp_path):
    summary = tree_io.import_memory_tree(str(tmp_path))
    assert summary == {"imported": 0, "linked": 0, "skipped_unfiled": 0}


_ROUNDTRIP_FIELDS = ("id", "title", "body", "kind", "tier", "scope",
                    "tags", "importance", "veracity")


def _assert_roundtripped(new_store, mid, orig, expected_links) -> None:
    got = new_store.get_dict(mid)
    assert got is not None, f"{mid} missing after import"
    for field in _ROUNDTRIP_FIELDS:
        assert got[field] == orig[field], field
    assert sorted(new_store.authoritative_topics_of(mid)) == expected_links


def _seed_roundtrip_memories(store):
    orphan_id = _remember("an orphan lesson", is_test=False)
    single_id = _remember("a singly-filed lesson", is_test=False,
                          tags=["gotcha"], importance=0.8, veracity="true")
    store.link_authoritative_topic(single_id, "leaf-a")
    multi_id = _remember("a multiply-filed lesson", is_test=False)
    store.link_authoritative_topic(multi_id, "leaf-b")
    store.link_authoritative_topic(multi_id, "leaf-a")
    return orphan_id, single_id, multi_id


def test_roundtrip_export_then_import_into_fresh_db(tmp_path, monkeypatch):
    _write_graph(tmp_path)
    store = memory.get_store()
    ids = _seed_roundtrip_memories(store)
    originals = {mid: store.get_dict(mid) for mid in ids}
    original_links = {mid: sorted(store.authoritative_topics_of(mid))
                      for mid in ids}

    tree_io.export_memory_tree(str(tmp_path))

    _reset_memory_db(tmp_path, monkeypatch, "reimport.db")
    assert memory.get_store().list_memories(include_tests=True) == []

    summary = tree_io.import_memory_tree(str(tmp_path))

    assert summary["imported"] == 3
    assert summary["skipped_unfiled"] == 1

    new_store = memory.get_store()
    for mid in ids:
        _assert_roundtripped(new_store, mid, originals[mid],
                             original_links[mid])


def test_import_is_idempotent_on_rerun(tmp_path, monkeypatch):
    _write_graph(tmp_path)
    store = memory.get_store()
    mid = _remember("idempotent-import lesson", is_test=False)
    store.link_authoritative_topic(mid, "leaf-a")
    tree_io.export_memory_tree(str(tmp_path))

    _reset_memory_db(tmp_path, monkeypatch, "reimport2.db")
    tree_io.import_memory_tree(str(tmp_path))
    tree_io.import_memory_tree(str(tmp_path))

    new_store = memory.get_store()
    assert len(new_store.list_memories(include_tests=True)) == 1
    assert new_store.authoritative_topics_of(mid) == ["leaf-a"]


# ── regression: bug 1 — stale exported files must be pruned ─────────────


def test_reexport_after_moving_link_removes_stale_file(tmp_path, monkeypatch):
    """A memory moved from leaf-a to leaf-b must not leave its old leaf-a
    file behind — and re-importing from the resulting tree must not
    resurrect the dropped leaf-a link."""
    _write_graph(tmp_path)
    store = memory.get_store()
    mid = _remember("a lesson that moves topics", is_test=False)
    store.link_authoritative_topic(mid, "leaf-a")

    tree_io.export_memory_tree(str(tmp_path))
    old_dir = tmp_path / tree_io.DEFAULT_TREE_DIR / "alpha" / "leaf-a"
    assert len(list(old_dir.glob("*.md"))) == 1

    store.unlink_authoritative_topic(mid, "leaf-a")
    store.link_authoritative_topic(mid, "leaf-b")
    tree_io.export_memory_tree(str(tmp_path))

    assert list(old_dir.glob("*.md")) == []
    new_dir = tmp_path / tree_io.DEFAULT_TREE_DIR / "beta" / "leaf-b"
    assert len(list(new_dir.glob("*.md"))) == 1

    _reset_memory_db(tmp_path, monkeypatch, "moved_link.db")
    tree_io.import_memory_tree(str(tmp_path))
    new_store = memory.get_store()
    assert new_store.authoritative_topics_of(mid) == ["leaf-b"]


def test_reexport_after_retiring_memory_removes_its_files(tmp_path):
    """A memory that goes active -> retired between two export runs must
    have its previously-written file(s) removed from the tree — the export
    tree's whole contract is that it mirrors current DB state."""
    _write_graph(tmp_path)
    store = memory.get_store()
    mid = _remember("a lesson that gets retired", is_test=False)
    store.link_authoritative_topic(mid, "leaf-a")

    tree_io.export_memory_tree(str(tmp_path))
    leaf_dir = tmp_path / tree_io.DEFAULT_TREE_DIR / "alpha" / "leaf-a"
    assert len(list(leaf_dir.glob("*.md"))) == 1

    store.update(mid, status="retired")
    tree_io.export_memory_tree(str(tmp_path))

    assert list(leaf_dir.glob("*.md")) == []


def test_reexport_after_deleting_memory_removes_its_files(tmp_path):
    """A hard-deleted memory's stale files must also be pruned."""
    _write_graph(tmp_path)
    store = memory.get_store()
    mid = _remember("a lesson that gets deleted", is_test=False)
    store.link_authoritative_topic(mid, "leaf-a")

    tree_io.export_memory_tree(str(tmp_path))
    leaf_dir = tmp_path / tree_io.DEFAULT_TREE_DIR / "alpha" / "leaf-a"
    assert len(list(leaf_dir.glob("*.md"))) == 1

    store.forget(mid)
    tree_io.export_memory_tree(str(tmp_path))

    assert list(leaf_dir.glob("*.md")) == []


# ── regression: bug 2 — canonical fallback must not discard other links ──


def test_canonical_fallback_skips_deleted_node_keeps_other_link(tmp_path):
    """leaf-a < leaf-z lexicographically, so leaf-a would normally be
    canonical. Deleting leaf-a from the graph must fall back to leaf-z (the
    other still-valid link), not to `_unfiled/`, and must leave the DB link
    to the deleted leaf-a node untouched."""
    graph = _write_graph(tmp_path)
    graph["topics"]["leaf-z"] = _leaf("Leaf Z", "beta")
    save_graph(tmp_path, graph)
    store = memory.get_store()
    mid = _remember("a lesson linked to a soon-deleted node", is_test=False)
    store.link_authoritative_topic(mid, "leaf-a")
    store.link_authoritative_topic(mid, "leaf-z")

    # Simulate leaf-a being deleted from the topic graph since the last
    # export — the DB link is untouched, only the graph shrinks.
    del graph["topics"]["leaf-a"]
    save_graph(tmp_path, graph)

    summary = tree_io.export_memory_tree(str(tmp_path))

    assert summary == {"canonical": 1, "stub": 0, "unfiled": 0}
    canon_files = list(
        (tmp_path / tree_io.DEFAULT_TREE_DIR / "beta" / "leaf-z").glob("*.md"))
    assert len(canon_files) == 1
    unfiled_files = list(
        (tmp_path / tree_io.DEFAULT_TREE_DIR / UNFILED_DIR).glob("*.md"))
    assert unfiled_files == []
    # The DB link to the now-deleted node is untouched — only the export
    # representation dropped it.
    assert sorted(store.authoritative_topics_of(mid)) == ["leaf-a", "leaf-z"]


# ── regression: bug 3 — filenames must be collision-safe ─────────────────


def test_same_title_slug_distinct_ids_produce_distinct_files(tmp_path):
    """Two memories with the same title-slug must not overwrite each
    other's file on disk (previously: an 8-char id prefix collision could
    silently drop one file with no error)."""
    _write_graph(tmp_path)
    m1 = _remember("Duplicate Title", is_test=False, title="Duplicate Title")
    m2 = _remember("Duplicate Title", is_test=False, title="Duplicate Title")
    memory.get_store().link_authoritative_topic(m1, "leaf-a")
    memory.get_store().link_authoritative_topic(m2, "leaf-a")
    assert m1 != m2

    summary = tree_io.export_memory_tree(str(tmp_path))

    assert summary == {"canonical": 2, "stub": 0, "unfiled": 0}
    files = list(
        (tmp_path / tree_io.DEFAULT_TREE_DIR / "alpha" / "leaf-a").glob("*.md"))
    assert len(files) == 2
    names = {f.name for f in files}
    assert len(names) == 2
    assert all(m1 in n or m2 in n for n in names)


# ── regression: bug 4 — reported `linked` count must not double-count ────


def test_import_linked_count_matches_actual_db_links(tmp_path, monkeypatch):
    """`linked` in the summary must equal the number of DISTINCT
    (memory_id, topic_node_id) rows that exist after import, not the
    number of `link_authoritative_topic` calls made (the canonical pass
    already links `also_filed_under` nodes; the stub pass re-links the
    same nodes and must not be counted twice)."""
    _write_graph(tmp_path)
    store = memory.get_store()
    single_id = _remember("a singly-filed lesson", is_test=False)
    store.link_authoritative_topic(single_id, "leaf-a")
    multi_id = _remember("a multiply-filed lesson", is_test=False)
    store.link_authoritative_topic(multi_id, "leaf-a")
    store.link_authoritative_topic(multi_id, "leaf-b")

    tree_io.export_memory_tree(str(tmp_path))

    _reset_memory_db(tmp_path, monkeypatch, "linked_count.db")
    summary = tree_io.import_memory_tree(str(tmp_path))

    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import MemoryAuthoritativeTopic
    from sqlmodel import select

    with MemorySessionLocal() as session:
        actual_links = len(session.exec(select(MemoryAuthoritativeTopic)).all())

    assert summary["linked"] == actual_links


# ── regression: bug 5 — scoped re-export must not prune OTHER scopes ─────


def test_scoped_reexport_does_not_prune_other_scope_files(tmp_path):
    """A `scope="repo:x"` export must never delete a still-active
    `global`-scope memory's file just because that memory isn't eligible
    under THIS run's scope filter -- `_existing_tree_files` scans the whole
    tree with no scope awareness, so the prune decision has to be made
    against a scope-unaware "still active somewhere" set, not the
    scope-filtered `rows` for this particular call."""
    _write_graph(tmp_path)
    store = memory.get_store()
    global_id = _remember("a global lesson", is_test=False, scope="global")
    store.link_authoritative_topic(global_id, "leaf-a")
    repo_id = _remember("a repo-scoped lesson", is_test=False, scope="repo:x")
    store.link_authoritative_topic(repo_id, "leaf-b")

    # Unfiltered export: both land on disk.
    tree_io.export_memory_tree(str(tmp_path))
    global_dir = tmp_path / tree_io.DEFAULT_TREE_DIR / "alpha" / "leaf-a"
    repo_dir = tmp_path / tree_io.DEFAULT_TREE_DIR / "beta" / "leaf-b"
    assert len(list(global_dir.glob("*.md"))) == 1
    assert len(list(repo_dir.glob("*.md"))) == 1

    # A scope-filtered re-export for repo:x only must leave the still-active
    # global-scope memory's file untouched.
    tree_io.export_memory_tree(str(tmp_path), scope="repo:x")

    assert len(list(global_dir.glob("*.md"))) == 1, (
        "scoped export must not delete another scope's active memory file")
    assert len(list(repo_dir.glob("*.md"))) == 1


def test_scoped_reexport_still_prunes_genuinely_retired_memory(tmp_path):
    """The fix for bug 5 must not regress the original stale-file-pruning
    fix: a memory that's genuinely retired (gone from EVERY scope) must
    still have its stale file(s) removed by a scope-filtered export run,
    even one that wouldn't otherwise touch that memory's scope."""
    _write_graph(tmp_path)
    store = memory.get_store()
    retiring_id = _remember("a repo-scoped lesson that gets retired",
                            is_test=False, scope="repo:x")
    store.link_authoritative_topic(retiring_id, "leaf-a")
    other_id = _remember("an unrelated repo-scoped lesson", is_test=False,
                        scope="repo:x")
    store.link_authoritative_topic(other_id, "leaf-b")

    tree_io.export_memory_tree(str(tmp_path))
    retiring_dir = tmp_path / tree_io.DEFAULT_TREE_DIR / "alpha" / "leaf-a"
    other_dir = tmp_path / tree_io.DEFAULT_TREE_DIR / "beta" / "leaf-b"
    assert len(list(retiring_dir.glob("*.md"))) == 1
    assert len(list(other_dir.glob("*.md"))) == 1

    store.update(retiring_id, status="retired")
    tree_io.export_memory_tree(str(tmp_path), scope="repo:x")

    assert list(retiring_dir.glob("*.md")) == []
    assert len(list(other_dir.glob("*.md"))) == 1
