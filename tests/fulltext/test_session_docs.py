"""Cross-session digests and the touched-file candidate set."""

from __future__ import annotations

import json
import sqlite3

import pytest

from lib.fulltext.session_docs import search_sessions, touched_files


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE session_spans (id INTEGER PRIMARY KEY, trace_id TEXT, "
        "span_id TEXT, name TEXT, attributes TEXT, duration_ms INTEGER, "
        "agent_id TEXT)"
    )
    c.execute(
        "CREATE TABLE sessions (trace_id TEXT PRIMARY KEY, title TEXT, "
        "last_seen TEXT, span_count INTEGER)"
    )
    return c


def _span(conn, trace_id, name, attrs, span_id):
    conn.execute(
        "INSERT INTO session_spans (trace_id, span_id, name, attributes, "
        "duration_ms, agent_id) VALUES (?,?,?,?,100,NULL)",
        (trace_id, span_id, name, json.dumps(attrs)),
    )


def _session(conn, trace_id, title, last_seen="2026-08-30T10:00:00", count=9):
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                 (trace_id, title, last_seen, count))


def test_digest_quotes_matching_spans_never_the_whole_session(conn):
    _session(conn, "t1", "Rate limiting")
    _span(conn, "t1", "tool.Bash", {"command": "redis-cli ping"}, "a")
    _span(conn, "t1", "tool.Bash", {"command": "unrelated noise"}, "b")
    (hit,) = search_sessions(conn, "redis", 6)
    assert "redis-cli ping" in hit.doc.body
    assert "unrelated noise" not in hit.doc.body


def test_row_deep_links_to_a_matching_span_of_that_session(conn):
    _session(conn, "t1", "S")
    _span(conn, "t1", "tool.Bash", {"command": "redis"}, "sp9")
    (hit,) = search_sessions(conn, "redis", 6)
    assert hit.doc.href == "/trace/sessions/t1?span=sp9"


def test_the_open_session_is_excluded_from_the_sessions_group(conn):
    _session(conn, "t1", "open")
    _session(conn, "t2", "other")
    _span(conn, "t1", "tool.Bash", {"command": "redis"}, "a")
    _span(conn, "t2", "tool.Bash", {"command": "redis"}, "b")
    hits = search_sessions(conn, "redis", 6, exclude_traces=["t1"])
    assert [h.doc.id for h in hits] == ["t2"]


def test_every_alias_of_the_open_session_is_excluded_not_just_the_first(conn):
    # An SDK-launched run is traced under both `sdk-<hex>` and the child's own
    # id; excluding one left the session you are already in listed as another.
    _session(conn, "child", "open")
    _session(conn, "sdk-9f", "open (sdk leg)")
    _session(conn, "t2", "other")
    _span(conn, "child", "tool.Bash", {"command": "redis"}, "a")
    _span(conn, "sdk-9f", "tool.Bash", {"command": "redis"}, "b")
    _span(conn, "t2", "tool.Bash", {"command": "redis"}, "c")
    hits = search_sessions(conn, "redis", 6, exclude_traces=["child", "sdk-9f"])
    assert [h.doc.id for h in hits] == ["t2"]


def test_matching_span_count_is_reported_separately_from_total(conn):
    _session(conn, "t1", "S", count=400)
    for i in range(3):
        _span(conn, "t1", "tool.Bash", {"command": "redis"}, f"s{i}")
    (hit,) = search_sessions(conn, "redis", 6)
    assert "3 matching spans" in hit.doc.sub
    assert "400 spans" in hit.doc.tag


def test_session_without_a_row_falls_back_to_its_trace_id(conn):
    _span(conn, "orphan-trace-id", "tool.Bash", {"command": "redis"}, "a")
    (hit,) = search_sessions(conn, "redis", 6)
    assert hit.doc.title == "orphan-trac"[:12] or hit.doc.title.startswith("orphan")


def test_touched_files_returns_the_open_sessions_files_regardless_of_query(conn):
    _span(conn, "t1", "tool.Edit",
          {"file_path": "/a/unrelated.py", "additions": 2}, "e1")
    rows = touched_files(conn, "redis", 6, trace_ids=["t1"])
    assert [r["path"] for r in rows] == ["/a/unrelated.py"]


def test_touched_files_reaches_other_sessions_only_when_unscoped(conn):
    _span(conn, "t2", "tool.Edit", {"file_path": "/a/hit.py", "diff": "+redis"}, "e1")
    _span(conn, "t2", "tool.Edit", {"file_path": "/a/miss.py", "diff": "+other"}, "e2")
    rows = touched_files(conn, "redis", 6, trace_ids=[])
    assert [r["path"] for r in rows] == ["/a/hit.py"]


def test_a_session_scoped_candidate_set_never_reaches_another_session(conn):
    # The union this replaced let /a/hit.py rank under a group labelled with
    # THIS session, and its row deep-linked to t2 — navigating the reader out
    # of the session they had just scoped to.
    _span(conn, "t1", "tool.Edit", {"file_path": "/a/mine.py"}, "e0")
    _span(conn, "t2", "tool.Edit", {"file_path": "/a/hit.py", "diff": "+redis"}, "e1")
    rows = touched_files(conn, "redis", 6, trace_ids=["t1"])
    assert [r["path"] for r in rows] == ["/a/mine.py"]


def test_touched_files_deduplicates_a_repeatedly_edited_path(conn):
    for i in range(4):
        _span(conn, "t1", "tool.Edit", {"file_path": "/a/x.py"}, f"e{i}")
    assert len(touched_files(conn, "q", 6, trace_ids=["t1"])) == 1


def test_touched_files_ignores_non_edit_spans(conn):
    _span(conn, "t1", "tool.Read", {"file_path": "/a/read.py"}, "r1")
    assert touched_files(conn, "q", 6, trace_ids=["t1"]) == []


def test_a_session_whose_readable_body_holds_no_match_is_dropped(conn):
    """The SQL pre-filter matches raw JSON, so it can hit an attribute the
    rendered body never shows — that session must not surface with 0 matches."""
    _session(conn, "t1", "untitled")
    _span(conn, "t1", "tool.Bash", {"unrendered_key": "redis"}, "a")
    assert search_sessions(conn, "redis", 6) == []


def test_a_hit_late_in_a_span_body_still_reaches_the_digest(conn):
    _session(conn, "t1", "S")
    _span(conn, "t1", "tool.Bash",
          {"stdout": "\n".join(["pad"] * 9 + ["redis found"])}, "a")
    (hit,) = search_sessions(conn, "redis", 6)
    assert "redis found" in hit.doc.body
    assert hit.hits >= 1
