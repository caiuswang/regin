"""Unit tests for the send_to_user message store (lib.agent_messages.store).

The autouse `tmp_db` fixture applies db/schema.sql to an isolated SQLite
file, so `agent_messages` exists and every row lands in tmp_path.
"""

from __future__ import annotations

from lib.agent_messages import store


def _record(trace_id="s1", body="hello", **kw):
    return store.record_message(trace_id=trace_id, body=body,
                                dispatch_webhook=False, **kw)


def test_record_inserts_and_serializes():
    m = _record(msg_type="result", title="Done")
    assert m["id"] is not None
    assert m["msg_type"] == "result"
    assert m["title"] == "Done"
    assert m["version"] == 1
    assert m["read_at"] is None


def test_missing_trace_id_returns_none():
    assert store.record_message(trace_id="", body="x",
                                dispatch_webhook=False) is None


def test_invalid_type_falls_back_to_progress():
    assert _record(msg_type="nonsense")["msg_type"] == "progress"


def test_supersede_by_key_collapses_in_place():
    a = _record(body="building… 40%", msg_type="progress", msg_key="build")
    b = _record(body="done ✓", msg_type="result", msg_key="build")
    assert a["id"] == b["id"]          # same row reused
    assert b["version"] == 2           # version bumped
    feed = store.list_session_messages("s1")
    assert len(feed) == 1              # one card, not two
    assert feed[0]["body"] == "done ✓"


def test_supersede_resets_unread():
    a = _record(msg_key="k")
    store.mark_read([a["id"]])
    assert store.unread_count() == 0
    _record(body="update", msg_key="k")   # supersede
    assert store.unread_count() == 1       # re-surfaced as unread


def test_links_normalized_to_label_href():
    m = _record(links=["tests/x.py", {"label": "PR", "href": "http://h/1"}])
    assert m["links"] == [
        {"label": "tests/x.py", "href": "tests/x.py"},
        {"label": "PR", "href": "http://h/1"},
    ]


def test_inbox_excludes_tests_by_default():
    _record(trace_id="real", body="prod", is_test=False)
    _record(trace_id="test", body="synthetic", is_test=True)
    default = store.list_inbox()
    assert all(not m["is_test"] for m in default)
    with_tests = store.list_inbox(include_tests=True)
    assert any(m["is_test"] for m in with_tests)


def test_inbox_carries_session_title_key():
    _record()
    assert "session_title" in store.list_inbox()[0]


def test_unread_count_and_mark_read():
    a = _record()
    b = _record(body="two")
    assert store.unread_count() == 2
    assert store.mark_read([a["id"], b["id"]]) == 2
    assert store.unread_count() == 0


def test_dismiss_removes_from_inbox_and_feed():
    a = _record()
    store.dismiss(a["id"])
    assert store.list_inbox() == []
    assert store.list_session_messages("s1") == []


def test_unread_only_filter():
    a = _record()
    _record(body="two")
    store.mark_read([a["id"]])
    assert len(store.list_inbox(unread_only=True)) == 1
