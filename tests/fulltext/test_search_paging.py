"""Every group reports its exact `total` and pages with `offset`."""

from __future__ import annotations

import json
import sqlite3

import pytest

from lib.fulltext import SCOPE_GLOBAL, SPAN_LIMIT, search

SESSIONS = 20


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
    for n in range(SESSIONS):
        trace_id = f"t{n:02d}"
        c.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                  (trace_id, f"Session {n}", "2026-10-01T10:00:00", 3))
        c.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, attributes, "
            "duration_ms, agent_id) VALUES (?,?,'tool.Bash',?,10,NULL)",
            (trace_id, f"sp{n:02d}", json.dumps({"command": "palette build"})),
        )
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, "patterns_dir", tmp_path / "none")
    monkeypatch.setattr(settings_mod.settings, "skills_dir", tmp_path / "none2")
    monkeypatch.setattr(settings_mod.settings, "repo_paths", [])
    return c


def _sessions(conn, limit, offset=0):
    (group,) = search(conn, "palette", ["session"], [], limit,
                      SCOPE_GLOBAL, offset)["groups"]
    return group


def test_session_total_is_not_capped_at_twice_the_limit(conn):
    group = _sessions(conn, limit=6)
    assert group["total"] == SESSIONS
    assert len(group["items"]) == 6
    assert group["offset"] == 0


def test_offset_pages_without_overlap(conn):
    pages = [_sessions(conn, limit=6, offset=o)["items"] for o in (0, 6, 12, 18)]
    ids = [item["id"] for page in pages for item in page]
    assert len(ids) == len(set(ids)) == SESSIONS
    whole = [i["id"] for i in _sessions(conn, limit=25)["items"]]
    assert ids == whole


def test_last_page_reports_end(conn):
    last = _sessions(conn, limit=12, offset=12)
    assert len(last["items"]) == SESSIONS - 12
    assert last["offset"] + len(last["items"]) == last["total"]
    past = _sessions(conn, limit=12, offset=40)
    assert past["items"] == []
    assert past["total"] == SESSIONS and past["offset"] == 40


def test_span_scope_unchanged(conn):
    for n in range(SPAN_LIMIT + 5):
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, attributes, "
            "duration_ms, agent_id) VALUES ('t00',?,'tool.Bash',?,10,NULL)",
            (f"extra{n}", json.dumps({"command": "palette again"})),
        )
    first = search(conn, "palette", ["span"], ["t00"])["groups"][0]
    paged = search(conn, "palette", ["span"], ["t00"], offset=12)["groups"][0]
    assert paged["items"] == first["items"]
    assert paged["total"] == first["total"]
    assert len(first["items"]) == SPAN_LIMIT
    assert first["offset"] == 0 and paged["offset"] == 0


@pytest.mark.parametrize("raw, expected", [
    ("", 0), ("12", 12), ("-3", 0), ("nope", 0),
])
def test_endpoint_reads_offset_as_a_non_negative_int(flask_client, monkeypatch,
                                                     raw, expected):
    from web.blueprints import search as search_bp_mod
    captured = {}

    def fake_search(conn, q, kinds, trace_ids, limit, scope, offset=0):
        captured.update(limit=limit, offset=offset)
        return {"q": q, "groups": []}

    monkeypatch.setattr(search_bp_mod, "search", fake_search)
    flask_client.get(f"/api/search?q=palette&limit=99&offset={raw}")
    assert captured == {"limit": search_bp_mod.MAX_LIMIT, "offset": expected}


def _pages(conn, kind, trace_ids, scope, total_expected, limit=6):
    seen = []
    for offset in range(0, total_expected, limit):
        (group,) = search(conn, "palette", [kind], trace_ids, limit, scope,
                          offset)["groups"]
        assert group["total"] == total_expected
        seen.extend(item["id"] for item in group["items"])
    return seen


def test_file_pages_are_disjoint_when_counts_tie(conn, tmp_path):
    paths = []
    for n in range(30):
        path = tmp_path / f"f{n:02d}.txt"
        path.write_text("palette\n")
        paths.append(str(path))
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, attributes, "
            "duration_ms, agent_id) VALUES ('t00',?,'tool.Edit',?,10,NULL)",
            (f"ed{n:02d}", json.dumps({"file_path": str(path)})),
        )
    for _ in range(3):
        ids = _pages(conn, "file", ["t00"], "session", len(paths))
        assert sorted(ids) == sorted(paths)
        assert ids[0] == paths[-1]


def test_pattern_pages_are_disjoint_when_counts_tie(conn, tmp_path,
                                                    monkeypatch):
    from lib import settings as settings_mod
    patterns = tmp_path / "patterns"
    patterns.mkdir()
    for n in range(20):
        (patterns / f"p{n:02d}.md").write_text(f"# Guide {n}\n\npalette\n")
    monkeypatch.setattr(settings_mod.settings, "patterns_dir", patterns)
    for _ in range(3):
        ids = _pages(conn, "pattern", [], SCOPE_GLOBAL, 20)
        assert len(set(ids)) == len(ids) == 20
