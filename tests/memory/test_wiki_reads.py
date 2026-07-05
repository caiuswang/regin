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


def _seed(spans: list[SessionSpan]) -> None:
    with SessionLocal() as session:
        for span in spans:
            session.add(span)
        session.commit()


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
