"""The fan-out orchestrator and its HTTP surface."""

from __future__ import annotations

import json
import sqlite3

import pytest

from lib import fulltext
from lib.fulltext import SCOPE_GLOBAL, search
from lib.fulltext.docs import ENTITY_KINDS, KIND_ORDER, MIN_QUERY_CHARS


@pytest.fixture
def conn(tmp_path, monkeypatch):
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
    c.execute("INSERT INTO sessions VALUES ('t1','Open','2026-08-30',5)")
    c.execute(
        "INSERT INTO session_spans (trace_id, span_id, name, attributes, "
        "duration_ms, agent_id) VALUES ('t1','sp1','tool.Bash',?,10,NULL)",
        (json.dumps({"command": "redis-cli ping"}),),
    )
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, "patterns_dir", tmp_path / "none")
    monkeypatch.setattr(settings_mod.settings, "skills_dir", tmp_path / "none2")
    monkeypatch.setattr(settings_mod.settings, "repo_paths", [])
    return c


def test_groups_render_in_the_fixed_kind_order(conn):
    payload = search(conn, "redis", None, ["t1"])
    assert [g["kind"] for g in payload["groups"]] == [
        k for k in KIND_ORDER if k not in ENTITY_KINDS]


def test_a_short_query_is_held_and_issues_no_group(conn):
    short = "r" * (MIN_QUERY_CHARS - 1)
    assert search(conn, short, None, ["t1"])["groups"] == []


def test_kinds_filter_narrows_the_fan_out(conn):
    payload = search(conn, "redis", ["span"], ["t1"])
    assert [g["kind"] for g in payload["groups"]] == ["span"]


def test_the_sessions_group_drops_the_whole_alias_group_of_the_open_session(conn):
    conn.execute("INSERT INTO sessions VALUES ('sdk-9f','Open (sdk)','2026-08-30',5)")
    conn.execute(
        "INSERT INTO session_spans (trace_id, span_id, name, attributes, "
        "duration_ms, agent_id) VALUES ('sdk-9f','sp2','tool.Bash',?,10,NULL)",
        (json.dumps({"command": "redis-cli ping"}),),
    )
    (group,) = search(conn, "redis", ["session"], ["t1", "sdk-9f"])["groups"]
    assert group["items"] == []


def test_global_scope_looks_past_the_open_session_for_spans_and_files(conn):
    # Same trace ids, opposite meaning: under `global` they are only the set
    # the `session` kind excludes, never a corpus to draw span/file rows from.
    payload = search(conn, "redis", ["span"], ["t1"], scope=SCOPE_GLOBAL)
    assert payload["groups"][0]["items"] == []


def test_the_sessions_kind_excludes_the_open_session_under_global_scope(conn):
    payload = search(conn, "redis", ["session"], ["t1"], scope=SCOPE_GLOBAL)
    assert payload["groups"][0]["items"] == []


def test_endpoint_defaults_to_session_scope_when_none_is_named(flask_client,
                                                              monkeypatch):
    from web.blueprints import search as search_bp_mod
    captured = {}

    def fake_search(conn, q, kinds, trace_ids, limit, scope, offset=0):
        captured["scope"] = scope
        return {"q": q, "groups": []}

    monkeypatch.setattr(search_bp_mod, "search", fake_search)
    flask_client.get("/api/search?q=redis&session=t1")
    assert captured["scope"] == "session"
    flask_client.get("/api/search?q=redis&session=t1&scope=global")
    assert captured["scope"] == "global"
    flask_client.get("/api/search?q=redis&session=t1&scope=nonsense")
    assert captured["scope"] == "session"


def test_span_group_finds_the_open_sessions_span(conn):
    (group,) = [g for g in search(conn, "redis", ["span"], ["t1"])["groups"]]
    assert [i["id"] for i in group["items"]] == ["sp1"]
    assert group["items"][0]["bodyHits"] == 1


def test_one_failing_corpus_leaves_the_others_usable(conn, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("corpus offline")
    monkeypatch.setattr(fulltext, "search_pattern_docs", boom)
    payload = search(conn, "redis", None, ["t1"])
    kinds = [g["kind"] for g in payload["groups"]]
    assert "pattern" not in kinds and "span" in kinds
    assert payload["failed"] == ["pattern"]


def test_body_is_shipped_as_a_window_not_the_whole_document(conn):
    conn.execute(
        "INSERT INTO session_spans (trace_id, span_id, name, attributes, "
        "duration_ms, agent_id) VALUES ('t1','big','tool.Bash',?,10,NULL)",
        (json.dumps({"stdout": "\n".join(["pad"] * 400 + ["redis"])}),),
    )
    (group,) = search(conn, "redis", ["span"], ["t1"])["groups"]
    body = next(i["body"] for i in group["items"] if i["id"] == "big")
    assert len(body.split("\n")) <= 60


def _seed_live_db():
    """Write one searchable span through the real connection the endpoint uses."""
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, title, span_count, started_at, "
            "last_seen) VALUES ('t1','Open',1,'2026-08-30T10:00:00',"
            "'2026-08-30T10:00:00')")
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
            "attributes) VALUES ('t1','sp1','tool.Bash','2026-08-30T10:00:00',?)",
            (json.dumps({"command": "redis-cli ping"}),))
        conn.commit()
    finally:
        conn.close()


def test_endpoint_returns_the_wire_shape(flask_client):
    _seed_live_db()
    res = flask_client.get("/api/search?q=redis&session=t1&kinds=span")
    assert res.status_code == 200
    body = res.get_json()
    assert body["q"] == "redis"
    assert body["groups"][0]["kind"] == "span"
    assert [i["id"] for i in body["groups"][0]["items"]] == ["sp1"]


def test_endpoint_holds_a_one_character_query(flask_client):
    body = flask_client.get("/api/search?q=r").get_json()
    assert body["groups"] == []


def test_endpoint_clamps_an_absurd_limit(flask_client, monkeypatch):
    from web.blueprints import search as search_bp_mod
    captured = {}

    def fake_search(conn, q, kinds, trace_ids, limit, scope, offset=0):
        captured["limit"] = limit
        return {"q": q, "groups": []}

    monkeypatch.setattr(search_bp_mod, "search", fake_search)
    flask_client.get("/api/search?q=redis&limit=9999")
    assert captured["limit"] == search_bp_mod.MAX_LIMIT


def test_endpoint_ignores_an_unknown_kind(flask_client, monkeypatch):
    from web.blueprints import search as search_bp_mod
    captured = {}

    def fake_search(conn, q, kinds, trace_ids, limit, scope, offset=0):
        captured["kinds"] = kinds
        return {"q": q, "groups": []}

    monkeypatch.setattr(search_bp_mod, "search", fake_search)
    flask_client.get("/api/search?q=redis&kinds=span,bogus")
    assert captured["kinds"] == ["span"]


def test_endpoint_requires_authentication(anon_client):
    assert anon_client.get("/api/search?q=redis").status_code == 401
