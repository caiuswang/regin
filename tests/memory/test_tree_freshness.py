"""The on-write export guard: the tree must be current before the next read."""

from pathlib import Path

import lib.memory as memory
from lib.memory import tree_io
from tests.memory.test_tree_io import _remember, _write_graph


def test_first_export_writes_tree_and_stamp(tmp_path):
    _write_graph(tmp_path)
    _remember("a lesson worth exporting", is_test=False)

    counts = tree_io.export_tree_if_stale(str(tmp_path))

    assert counts is not None and counts["canonical"] >= 1
    assert (Path(tmp_path) / tree_io.DEFAULT_TREE_DIR
            / tree_io.STAMP_FILE).is_file()


def test_second_export_with_no_writes_is_a_noop(tmp_path):
    _write_graph(tmp_path)
    _remember("a lesson worth exporting", is_test=False)
    tree_io.export_tree_if_stale(str(tmp_path))

    assert tree_io.export_tree_if_stale(str(tmp_path)) is None


def test_a_new_memory_makes_the_tree_stale_again(tmp_path):
    _write_graph(tmp_path)
    _remember("the first lesson", is_test=False)
    tree_io.export_tree_if_stale(str(tmp_path))

    _remember("a second lesson written mid-session", is_test=False)

    counts = tree_io.export_tree_if_stale(str(tmp_path))
    assert counts is not None, "a fresh memory must re-export the tree"
    assert counts["canonical"] >= 2


def test_signature_changes_when_a_memory_is_added(tmp_path):
    _write_graph(tmp_path)
    _remember("first", is_test=False)
    before = tree_io.store_signature()
    _remember("second", is_test=False)
    assert tree_io.store_signature() != before


def test_export_failure_is_swallowed_not_raised(tmp_path, monkeypatch):
    """Runs on the lesson write path — a broken export must not lose the write."""
    _write_graph(tmp_path)
    _remember("a lesson", is_test=False)
    monkeypatch.setattr(tree_io, "export_memory_tree",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))

    assert tree_io.export_tree_if_stale(str(tmp_path)) is None


def test_stamp_is_not_mistaken_for_a_memory_file(tmp_path):
    """The stamp lives in the tree dir; the importer must ignore it."""
    _write_graph(tmp_path)
    _remember("a lesson", is_test=False)
    tree_io.export_tree_if_stale(str(tmp_path))

    summary = tree_io.import_memory_tree(str(tmp_path))
    assert summary["imported"] >= 1
    memory.get_store()  # import must not have raised on the stamp file


def test_linking_a_topic_makes_the_tree_stale(tmp_path):
    """A memory's file PATH comes from its topic links, and linking never
    bumps Memory.updated_at — so a memories-only signature left a lesson
    captured unclassified stranded in `_unfiled/` forever."""
    _write_graph(tmp_path)
    mid = _remember("a lesson captured before classification", is_test=False)
    tree_io.export_tree_if_stale(str(tmp_path))

    memory.get_store().link_authoritative_topic(mid, "leaf-a")

    counts = tree_io.export_tree_if_stale(str(tmp_path))
    assert counts is not None, "a new topic link must re-export the tree"
    assert counts["unfiled"] == 0


def test_signature_is_scoped(tmp_path):
    _write_graph(tmp_path)
    _remember("a lesson", is_test=False)
    assert tree_io.store_signature("repo:other") != tree_io.store_signature()
