"""`lib/palette/resolve.py` — R7 address syntax (f146 S2)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from lib.palette.resolve import KEEP_TYPING, SESSION_PREFIX_LIMIT, resolve


@pytest.fixture
def conn(tmp_db):
    c = sqlite3.connect(str(tmp_db))
    yield c
    c.close()


def _feature(conn, fid, title, body=None):
    conn.execute("INSERT INTO features (id, title, body) VALUES (?, ?, ?)",
                 (fid, title, body))


def _task(conn, tid, title, body=None):
    conn.execute("INSERT INTO agent_tasks (id, title, body, status, source) "
                 "VALUES (?, ?, ?, 'idea', 'native')", (tid, title, body))


def _defect(conn, did, symptom):
    conn.execute("INSERT INTO defects (id, symptom, symptom_key) VALUES (?, ?, ?)",
                 (did, symptom, f"k{did}"))


def _session(conn, trace_id, title, last_seen="2026-10-01T10:00:00"):
    conn.execute(
        "INSERT INTO sessions (trace_id, title, title_source, status, started_at, "
        " last_seen, cwd) VALUES (?, ?, 'user', 'ended', '2026-10-01T00:00:00', ?, '/r')",
        (trace_id, title, last_seen))


def _span(conn, trace_id, span_id, attrs):
    conn.execute(
        "INSERT INTO session_spans (trace_id, span_id, name, kind, start_time, "
        " status_code, attributes) VALUES (?, ?, 'tool.Bash', 'internal', "
        " '2026-10-01T10:00:00', 'OK', ?)", (trace_id, span_id, json.dumps(attrs)))


def _refs(rows):
    return [r["ref"] for r in rows]


def _text(payload):
    return {g["kind"]: _refs(g["items"]) for g in payload["text"]}


@pytest.mark.parametrize("ref", ["F146", "f146", "f:146", "F:146"])
def test_entity_ref_resolves_exactly(conn, ref):
    _feature(conn, 146, "Search palette")
    _feature(conn, 145, "Neighbour")
    _task(conn, 425, "Address resolver", body="covers F146")
    conn.commit()
    payload = resolve(conn, ref)
    assert payload["address"] == "feature"
    assert _refs(payload["hits"]) == ["F146"]
    assert payload["hits"][0]["match"] == "id"
    assert "miss" not in payload
    # Screen 4/5: an exact hit is the one row; text quoting the ref is not shown.
    assert payload["text"] == []


def test_bare_number_resolves_every_kind(conn):
    _feature(conn, 146, "Search palette")
    _task(conn, 146, "Search index v1")
    _defect(conn, 146, "Sidebar collapses on reload")
    _task(conn, 425, "Address resolver", body="covers F146 and bare 146")
    _session(conn, "74ce2a90aaaa", "Ship round 2")
    _span(conn, "74ce2a90aaaa", "s1", {"command": "regin feature ship 146"})
    conn.commit()

    payload = resolve(conn, "146")
    assert payload["address"] == "number"
    assert _refs(payload["hits"]) == ["F146", "T146", "D146"]
    assert {r["match"] for r in payload["hits"]} == {"id"}
    # Text that mentions the number follows, grouped by kind, and never
    # repeats a row already resolved by id.
    assert _text(payload) == {"task": ["T425"], "session": ["#74ce2a90"]}
    assert all(r["match"] == "text"
               for g in payload["text"] for r in g["items"])


def test_bare_number_resolves_only_the_kinds_that_have_it(conn):
    _task(conn, 7, "only a task")
    conn.commit()
    payload = resolve(conn, "7")
    assert _refs(payload["hits"]) == ["T7"]
    # One digit is under the text floor: no text pass, no noise.
    assert payload["text"] == []


def test_miss_lists_text_not_neighbours(conn):
    _task(conn, 998, "Neighbour below")
    _task(conn, 1000, "Neighbour above")
    _defect(conn, 12, "Crash on run 999\nstack trace attached")
    conn.commit()

    payload = resolve(conn, "T999")
    assert payload["address"] == "task"
    assert payload["miss"] == "No task T999"
    assert payload["hits"] == []
    assert _text(payload) == {"defect": ["D12"]}
    shown = _refs(payload["hits"]) + [r for refs in _text(payload).values()
                                      for r in refs]
    assert "T998" not in shown and "T1000" not in shown


def test_miss_with_no_text_is_a_bare_miss(conn):
    _task(conn, 998, "Neighbour below")
    conn.commit()
    payload = resolve(conn, "t:999")
    assert payload["miss"] == "No task T999"
    assert payload["hits"] == [] and payload["text"] == []


@pytest.mark.parametrize("ref", ["#", "#a", "#a1", "#A1b"])
def test_short_hash_is_not_searched(conn, ref, monkeypatch):
    def no_query(*a, **kw):
        raise AssertionError("a short hash must not query")
    monkeypatch.setattr("lib.palette.rows.session_rows", no_query)
    monkeypatch.setattr("lib.palette.resolve.search_sessions", no_query)
    payload = resolve(conn, ref)
    assert payload["address"] == "session"
    assert payload["pending"] == KEEP_TYPING
    assert payload["hits"] == [] and payload["text"] == []
    assert "miss" not in payload


def test_hash_prefix_lists_matching_sessions(conn):
    _session(conn, "a1b2c3d4-0001", "Older", last_seen="2026-10-01T09:00:00")
    _session(conn, "a1b2ffff-0002", "Newer", last_seen="2026-10-02T09:00:00")
    _session(conn, "b0000000-0003", "Elsewhere")
    conn.commit()
    payload = resolve(conn, "#A1B2")
    assert _refs(payload["hits"]) == ["#a1b2ffff", "#a1b2c3d4"]
    assert {r["match"] for r in payload["hits"]} == {"prefix"}
    assert resolve(conn, "#ffff")["miss"] == "No session #ffff"


def test_hash_prefix_matches_the_switchers_lookup(conn):
    _session(conn, "sdk-run_0001", "Hand-made id")
    _session(conn, "sdkXrun-0002", "Wildcard bait")
    conn.execute("UPDATE sessions SET is_test = 1 WHERE trace_id = 'sdkXrun-0002'")
    conn.commit()
    # Not only hex, and `_` is a literal rather than a LIKE wildcard.
    assert _refs(resolve(conn, "#sdk-run_")["hits"]) == ["#sdk-run_"]
    assert resolve(conn, "#sdkX")["hits"] == []


def test_hash_prefix_is_capped(conn):
    for n in range(SESSION_PREFIX_LIMIT + 3):
        _session(conn, f"cafe{n:04d}", f"s{n}")
    conn.commit()
    assert len(resolve(conn, "#cafe")["hits"]) == SESSION_PREFIX_LIMIT


@pytest.mark.parametrize("ref", ["palette", "F14x", "# a1b2", "", "9" * 19])
def test_non_address_is_left_to_text_search(conn, ref):
    payload = resolve(conn, ref)
    assert payload["address"] is None
    assert payload["hits"] == [] and payload["text"] == []


def test_resolve_endpoint(conn, flask_client):
    _defect(conn, 276, "Show all is unreachable")
    conn.commit()
    body = flask_client.get("/api/palette/resolve?ref=D276").get_json()
    assert _refs(body["hits"]) == ["D276"]
    assert body["hits"][0]["title"] == "Show all is unreachable"


def test_text_is_a_whole_number_mention_from_a_real_session(conn):
    _task(conn, 9, "Fix bug in F1460 rollout")
    _task(conn, 10, "Follow up on 146")
    _session(conn, "fixture-0001", "fixture")
    conn.execute("UPDATE sessions SET is_test = 1 WHERE trace_id = 'fixture-0001'")
    _span(conn, "fixture-0001", "f1", {"command": "ship 146"})
    _span(conn, "orphan-trace", "o1", {"command": "ship 146"})
    conn.commit()
    assert _text(resolve(conn, "146")) == {"task": ["T10"]}
