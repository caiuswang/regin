"""Directory refs — a whole subtree cited as one ref instead of one per file.

Covers the shared coverage rule (`ref_covers`), the containment-aware
shared-primary audit, the ref-kind validation that keeps `dir/` and `dir`
from blurring, scan's preservation of curated dir refs, and the readers that
must either follow a dir ref (wiki-debt scoping) or skip it (content-drift
and ref digests, which need a file body).
"""

from pathlib import Path

from lib.topics.content_drift import _changed_content
from lib.topics.core import is_dir_ref, ref_covers
from lib.topics.scan import refs_for_topic
from lib.topics.validation import audit_graph, validate_topic
from lib.topics.wiki_debt import _narrow


def _graph(**topics):
    base = {"label": "B", "intent": "x", "status": "active", "kind": "bucket"}
    return {"version": 1, "repo": "r",
            "topics": {"buck": base, **topics}}


def _topic(refs):
    return {"label": "T", "intent": "x", "status": "active",
            "parent_id": "buck", "refs": refs}


def _shared(graph):
    return [i for i in audit_graph(graph) if i.code == "graph.shared_primary_ref"]


# ── the coverage rule itself ─────────────────────────────────────────

def test_is_dir_ref_is_the_trailing_slash():
    assert is_dir_ref("lib/trace/payload_schemas/")
    assert not is_dir_ref("lib/trace/payload_schemas")
    assert not is_dir_ref(None)


def test_ref_covers_only_follows_directory_refs():
    assert ref_covers("a/b.py", "a/b.py")
    assert ref_covers("a/", "a/b.py")
    assert ref_covers("a/", "a/deep/b.py")
    # a file ref never covers anything but itself, even by prefix
    assert not ref_covers("a/b", "a/b.py")
    assert not ref_covers("a/b.py", "a/")


# ── audit: a dir ref must not hide a boundary collision ──────────────

def test_dir_ref_and_file_under_it_collide_once():
    g = _graph(
        owner=_topic([{"path": "schemas/"}]),
        other=_topic([{"path": "schemas/a.json"}]),
    )
    shared = _shared(g)
    assert len(shared) == 1                       # not one per covered file
    assert shared[0].paths == ("schemas/a.json",)  # keyed on the narrower path
    assert shared[0].topic_ids == ("other", "owner")


def test_two_topics_claiming_the_same_dir_collide_once():
    g = _graph(
        a=_topic([{"path": "schemas/"}]),
        b=_topic([{"path": "schemas/"}]),
    )
    shared = _shared(g)
    assert len(shared) == 1
    assert shared[0].paths == ("schemas/",)
    assert "directory schemas/" in shared[0].message


def test_reference_tier_under_a_dir_ref_is_exempt():
    g = _graph(
        owner=_topic([{"path": "schemas/"}]),
        pointer=_topic([{"path": "schemas/a.json", "tier": "reference"}]),
    )
    assert not _shared(g)


def test_collapsing_files_into_a_dir_ref_clears_the_warnings():
    # the regin case: two topics enumerating the same subtree file-by-file …
    files = [{"path": f"schemas/{n}.json"} for n in range(5)]
    assert len(_shared(_graph(a=_topic(files), b=_topic(list(files))))) == 5
    # … and the same boundary after one side collapses and the other drops it
    assert not _shared(_graph(a=_topic([{"path": "schemas/"}]), b=_topic([])))


# ── validation: `dir/` and `dir` must not blur ───────────────────────

def _kind_issues(tmp_path, path):
    topic = _topic([{"path": path}])
    return [i.code for i in validate_topic(
        topic, mode="approved", topic_id="t",
        graph_context=_ctx(tmp_path))]


def _ctx(tmp_path):
    from lib.topics.validation import GraphContext
    return GraphContext(topic_ids=frozenset({"t"}), alias_owners={},
                        repo_path=Path(tmp_path))


def test_directory_ref_needs_its_trailing_slash(tmp_path):
    (tmp_path / "schemas").mkdir()
    assert "topic.ref_kind_mismatch" in _kind_issues(tmp_path, "schemas")
    assert "topic.ref_kind_mismatch" not in _kind_issues(tmp_path, "schemas/")


def test_trailing_slash_on_a_file_is_a_mismatch(tmp_path):
    (tmp_path / "a.py").write_text("x\n")
    assert "topic.ref_kind_mismatch" in _kind_issues(tmp_path, "a.py/")
    assert "topic.ref_kind_mismatch" not in _kind_issues(tmp_path, "a.py")


def test_missing_directory_is_a_dead_ref_not_a_kind_mismatch(tmp_path):
    codes = _kind_issues(tmp_path, "gone/")
    assert "graph.dead_ref" in codes
    assert "topic.ref_kind_mismatch" not in codes


def test_noncanonical_paths_are_rejected_before_they_can_hide_overlap(tmp_path):
    # each of these `exists()` but compares unequal to the path git reports, so
    # a primary under it would slip past the boundary audit
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "a.json").write_text("{}\n")
    for path in ("schemas//", "./schemas/", "../", "/etc/", "schemas//a.json"):
        assert "topic.ref_path_not_canonical" in _kind_issues(tmp_path, path), path
    assert not _kind_issues(tmp_path, "schemas/a.json")
    assert not _kind_issues(tmp_path, "schemas/")


# ── scan: a curated dir ref survives a full reconcile ────────────────

def test_scan_keeps_a_dir_ref_and_drops_the_files_it_covers():
    topic = {"include_globs": ["schemas/**", "lib/x.py"],
             "refs": [{"path": "schemas/"}]}
    files = ["schemas/a.json", "schemas/deep/b.json", "lib/x.py"]
    paths = [r["path"] for r in refs_for_topic(files, topic)]
    assert paths == ["lib/x.py", "schemas/"]


def test_scan_without_a_dir_ref_still_enumerates():
    topic = {"include_globs": ["schemas/**"], "refs": []}
    paths = [r["path"] for r in refs_for_topic(["schemas/a.json"], topic)]
    assert paths == ["schemas/a.json"]


def test_an_explicitly_cited_file_survives_the_collapse():
    # the escape hatch `core.is_dir_ref` prescribes: pull one file back out of a
    # collapsed subtree by hand and a full rescan must not delete it again
    topic = {"include_globs": ["schemas/**"],
             "refs": [{"path": "schemas/"}, {"path": "schemas/README.md"}]}
    paths = [r["path"] for r in
             refs_for_topic(["schemas/README.md", "schemas/a.json"], topic)]
    assert paths == ["schemas/", "schemas/README.md"]


def test_scan_keeps_a_dir_ref_its_globs_no_longer_match():
    topic = {"include_globs": ["lib/x.py"], "refs": [{"path": "schemas/"}]}
    paths = [r["path"] for r in refs_for_topic(["lib/x.py"], topic)]
    assert paths == ["lib/x.py", "schemas/"]


# ── readers: follow a dir ref, or skip it ────────────────────────────

def test_wiki_debt_scopes_a_dir_ref_by_what_changed_beneath_it():
    paths = ["schemas/", "lib/x.py"]
    assert _narrow(paths, {"schemas/a.json"}) == ["schemas/"]
    assert _narrow(paths, {"other/a.json"}) == []
    assert _narrow(paths, None) == paths


def test_content_drift_skips_dir_refs(tmp_path):
    stored = {"schemas/": {"content_hash": "deadbeef"}}
    assert _changed_content(tmp_path, {"path": "schemas/"}, stored) is None


def test_deleting_a_file_under_a_dir_ref_still_cascades(monkeypatch):
    # the trailing slash must not make the deletion comparison silently false
    from lib.topics import drift

    graph = _graph(owner=_topic([{"path": "schemas/"}]))
    monkeypatch.setattr(drift, "load_authoritative_graph", lambda _p: graph,
                        raising=False)
    monkeypatch.setattr("lib.topics.graph_io.load_authoritative_graph",
                        lambda _p: graph)
    cascaded: list[str] = []
    monkeypatch.setattr("lib.memory.topic_cascade.cascade_topic_stale",
                        lambda store, tid, reason: cascaded.append(tid) or 1)

    assert drift.cascade_deletions(".", object(), {"schemas/a.json"}) == 1
    assert cascaded == ["owner"]
    assert drift.cascade_deletions(".", object(), {"elsewhere/a.json"}) == 0
