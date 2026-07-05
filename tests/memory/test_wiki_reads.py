"""Trace-derived 'read' wiki-recall signal (lib/memory/wiki_reads.py)."""

from __future__ import annotations

import json

import lib.memory as memory
from lib.memory.wiki_reads import (
    _topic_id_from_path,
    compute_wiki_reads,
    sync_wiki_reads,
)
from lib.orm import SessionLocal
from lib.orm.models.trace import SessionSpan


def test_topic_id_extraction_rejects_index_nested_and_nonwiki():
    base = "/Users/x/regin/.regin/topics/wiki"
    assert _topic_id_from_path(f"{base}/agent-memory.md") == "agent-memory"
    assert _topic_id_from_path(f"{base}/index.md") is None       # generated index
    assert _topic_id_from_path(f"{base}/sub/nested.md") is None   # not a leaf file
    assert _topic_id_from_path(f"{base}/foo.txt") is None         # not markdown
    assert _topic_id_from_path("/Users/x/regin/lib/foo.md") is None


def _read_span(span_id: str, attributes: dict, start_time: str,
               trace_id: str = "t1") -> SessionSpan:
    return SessionSpan(
        trace_id=trace_id, span_id=span_id, name="tool.Read",
        start_time=start_time, attributes=json.dumps(attributes))


def _fetch_span(span_id: str, node_id: str, start_time: str,
                trace_id: str = "t1", *, reinforce: bool | None = None,
                ) -> SessionSpan:
    tool_input: dict = {"node_id": node_id, "scope": "repo:regin"}
    if reinforce is not None:
        tool_input["reinforce"] = reinforce
    return SessionSpan(
        trace_id=trace_id, span_id=span_id,
        name="tool.mcp__memory__index_fetch",
        start_time=start_time, attributes=json.dumps({"tool_input": tool_input}))


def _seed(spans: list[SessionSpan]) -> None:
    with SessionLocal() as session:
        for span in spans:
            session.add(span)
        session.commit()


def _wiki_dir_with(tmp_path, monkeypatch, topics: list[str]):
    """Point `settings.project_root` at a tmp repo whose wiki dir holds a `.md`
    for each of `topics`, so the index_fetch existence guard sees real files."""
    from lib.settings import settings
    from lib.topics.wiki import wiki_dir
    monkeypatch.setattr(settings, "project_root", tmp_path)
    wdir = wiki_dir(tmp_path)
    wdir.mkdir(parents=True, exist_ok=True)
    for topic in topics:
        (wdir / f"{topic}.md").write_text(f"# {topic}\n")
    return wdir


def test_compute_counts_distinct_sessions_and_excludes_nonwiki():
    wiki = "/repo/.regin/topics/wiki"
    _seed([
        # alpha read in TWO distinct sessions -> count 2
        _read_span("a", {"file_path": f"{wiki}/alpha.md"}, "2026-01-01T00:00:00", "s1"),
        _read_span("b", {"file_path": f"{wiki}/alpha.md"}, "2026-01-02T00:00:00", "s2"),
        _read_span("c", {"file_path": f"{wiki}/beta.md"}, "2026-01-01T00:00:00", "s1"),
        _read_span("d", {"file_path": f"{wiki}/index.md"}, "2026-01-01T00:00:00", "s1"),
        # wiki path only in read CONTENT, not the file being read -> excluded
        _read_span("e", {"file_path": "/repo/lib/x.py",
                         "content": f"see {wiki}/alpha.md"}, "2026-01-03T00:00:00", "s1"),
        # unrelated read -> not even prefiltered
        _read_span("f", {"file_path": "/repo/lib/y.py"}, "2026-01-01T00:00:00", "s1"),
    ])
    agg = compute_wiki_reads()
    assert set(agg) == {"alpha", "beta"}
    assert agg["alpha"]["count"] == 2
    assert agg["alpha"]["last_read"] == "2026-01-02T00:00:00"  # max, not last-seen
    assert agg["beta"]["count"] == 1


def test_repeated_reads_in_one_session_count_once():
    """A wiki opened several times in ONE session — paginated line ranges or a
    re-read — is one consultation, not many (the anti-inflation invariant)."""
    wiki = "/repo/.regin/topics/wiki"
    _seed([
        _read_span("a", {"file_path": f"{wiki}/alpha.md",
                         "start_line": 1}, "2026-01-01T00:00:00", "s1"),
        _read_span("b", {"file_path": f"{wiki}/alpha.md",
                         "start_line": 200}, "2026-01-01T00:05:00", "s1"),
        _read_span("c", {"file_path": f"{wiki}/alpha.md",
                         "start_line": 400}, "2026-01-01T00:09:00", "s1"),
    ])
    agg = compute_wiki_reads()
    assert agg["alpha"]["count"] == 1
    assert agg["alpha"]["last_read"] == "2026-01-01T00:09:00"


def test_sync_is_idempotent_set_semantics():
    wiki = "/repo/.regin/topics/wiki"
    _seed([
        _read_span("a", {"file_path": f"{wiki}/alpha.md"}, "2026-01-01T00:00:00", "s1"),
        _read_span("b", {"file_path": f"{wiki}/alpha.md"}, "2026-01-02T00:00:00", "s2"),
    ])
    sync_wiki_reads()
    sync_wiki_reads()  # re-run must SET, not accumulate
    rows = memory.get_store().wiki_recall_stats(signal="read")
    assert len(rows) == 1
    assert rows[0].topic_id == "alpha" and rows[0].recall_count == 2


def test_sync_drops_read_rows_whose_reads_vanished():
    store = memory.get_store()
    # a stale read counter with no backing span
    store.replace_wiki_read_counts({"ghost": {"count": 9, "last_read": None}})
    assert any(r.topic_id == "ghost"
               for r in store.wiki_recall_stats(signal="read"))
    sync_wiki_reads()  # trace has no spans -> ghost must be pruned
    assert store.wiki_recall_stats(signal="read") == []


def test_sync_leaves_exposure_rows_untouched():
    store = memory.get_store()
    store.bump_wiki_recall("alpha", signal="exposure")
    sync_wiki_reads()
    exposure = store.wiki_recall_stats(signal="exposure")
    assert len(exposure) == 1 and exposure[0].recall_count == 1


def test_wiki_read_counts_keys_on_read_signal_only():
    store = memory.get_store()
    # exposure alone must NOT rank a wiki up — only genuine reads count
    store.bump_wiki_recall("alpha", signal="exposure")
    store.bump_wiki_recall("alpha", signal="exposure")
    store.replace_wiki_read_counts({"alpha": {"count": 3, "last_read": None}})
    store.bump_wiki_recall("beta", signal="exposure")  # exposure-only
    reads = store.wiki_read_counts()
    assert reads == {"alpha": 3}          # beta absent: surfaced but never read
    assert store.wiki_read_counts().get("absent", 0) == 0


def test_index_fetch_counts_as_read_without_a_tool_read(tmp_path, monkeypatch):
    """The primary consultation path: navigate to a leaf via index_fetch and
    never literally Read the .md. It must still earn a read (reproduces the
    a04efaa2 gap where 4 fetches produced 0 reads)."""
    _wiki_dir_with(tmp_path, monkeypatch, ["alpha", "beta"])
    _seed([
        _fetch_span("f1", "alpha", "2026-01-01T00:00:00", "s1"),
        _fetch_span("f2", "beta", "2026-01-02T00:00:00", "s2"),
    ])
    agg = compute_wiki_reads()
    assert set(agg) == {"alpha", "beta"}
    assert agg["alpha"]["count"] == 1
    assert agg["alpha"]["last_read"] == "2026-01-01T00:00:00"


def test_fetch_and_read_same_session_count_once(tmp_path, monkeypatch):
    """A session that both index_fetches and tool.Reads the same wiki is one
    distinct consultation, not two (union dedup across span types)."""
    wiki = "/repo/.regin/topics/wiki"
    _wiki_dir_with(tmp_path, monkeypatch, ["alpha"])
    _seed([
        _fetch_span("f1", "alpha", "2026-01-01T00:00:00", "s1"),
        _read_span("r1", {"file_path": f"{wiki}/alpha.md"},
                   "2026-01-01T00:05:00", "s1"),
    ])
    agg = compute_wiki_reads()
    assert agg["alpha"]["count"] == 1
    assert agg["alpha"]["last_read"] == "2026-01-01T00:05:00"  # max across both


def test_index_fetch_reinforce_false_does_not_count(tmp_path, monkeypatch):
    """An AUDIT / eval sweep (reinforce=False) surveys the tree without inflating
    the signal — mirrors recall's reinforce gate."""
    _wiki_dir_with(tmp_path, monkeypatch, ["alpha"])
    _seed([_fetch_span("f1", "alpha", "2026-01-01T00:00:00", "s1",
                       reinforce=False)])
    assert compute_wiki_reads() == {}


def test_index_fetch_on_bucket_without_wiki_creates_no_read(tmp_path, monkeypatch):
    """A fetch on a bucket / un-accepted topic (no .md on disk) is not a wiki
    read — mirrors index_fetch's own `if wiki_exists` guard."""
    _wiki_dir_with(tmp_path, monkeypatch, ["alpha"])       # 'bucket' absent
    _seed([
        _fetch_span("f1", "bucket", "2026-01-01T00:00:00", "s1"),
        _fetch_span("f2", "alpha", "2026-01-02T00:00:00", "s2"),
    ])
    assert set(compute_wiki_reads()) == {"alpha"}


def test_index_fetch_duplicate_spans_per_call_dedup(tmp_path, monkeypatch):
    """One index_fetch call emits two spans (tool_input dict + mcp_input JSON);
    both resolve to the same topic+session and collapse to one read."""
    _wiki_dir_with(tmp_path, monkeypatch, ["alpha"])
    _seed([
        _fetch_span("f1", "alpha", "2026-01-01T00:00:00", "s1"),
        SessionSpan(trace_id="s1", span_id="f2",
                    name="tool.mcp__memory__index_fetch",
                    start_time="2026-01-01T00:00:01",
                    attributes=json.dumps(
                        {"mcp_input": json.dumps({"node_id": "alpha"})})),
    ])
    assert compute_wiki_reads()["alpha"]["count"] == 1
