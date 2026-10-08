"""The feature / task / defect search kinds (f146 S2)."""

from __future__ import annotations

import sqlite3

import pytest

from lib.fulltext import ENTITY_FANOUT_CAP, SCOPE_GLOBAL, SCOPE_SESSION, search
from lib.fulltext.docs import ENTITY_KINDS, KIND_ORDER
from lib.fulltext.entity_docs import search_defects, search_features, search_tasks
from lib.orm.models.features import ABANDONED, SHIPPED


@pytest.fixture
def conn(tmp_db, tmp_path, monkeypatch):
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, "patterns_dir", tmp_path / "none")
    monkeypatch.setattr(settings_mod.settings, "skills_dir", tmp_path / "none2")
    monkeypatch.setattr(settings_mod.settings, "repo_paths", [])
    c = sqlite3.connect(str(tmp_db))
    yield c
    c.close()


def _feature(conn, fid, title, body=None, event=None):
    conn.execute("INSERT INTO features (id, title, body) VALUES (?, ?, ?)",
                 (fid, title, body))
    if event:
        conn.execute("INSERT INTO feature_events (feature_id, kind, declared_by) "
                     "VALUES (?, ?, 'operator')", (fid, event))


def _task(conn, tid, title, body=None, status="idea"):
    conn.execute("INSERT INTO agent_tasks (id, title, body, status, source) "
                 "VALUES (?, ?, ?, ?, 'native')", (tid, title, body, status))


def _defect(conn, did, symptom, *, missing=None, status="open", triage=None):
    conn.execute(
        "INSERT INTO defects (id, symptom, symptom_key, missing_check, status, "
        " triage_state) VALUES (?, ?, ?, ?, ?, ?)",
        (did, symptom, f"k{did}", missing, status, triage))


def _ids(scored):
    return [s.doc.id for s in scored]


def _seed_mixed(conn):
    _feature(conn, 146, "Search palette", body="Make the palette a navigator")
    _feature(conn, 147, "Unrelated", body="spec mentions the PALETTE once")
    _feature(conn, 148, "Nothing here")
    _task(conn, 421, "Resolver slice", body="wire the palette resolver")
    _task(conn, 422, "Other work")
    _defect(conn, 276, "Show all is unreachable\nthe palette stops at 6")
    _defect(conn, 277, "Sidebar collapses", missing="no palette test")
    _defect(conn, 278, "Unrelated defect")
    conn.commit()


def test_feature_task_defect_are_searchable(conn):
    _seed_mixed(conn)

    # A title hit outranks a body hit; equal scores read newest first.
    assert _ids(search_features(conn, "palette")) == ["146", "147"]
    assert _ids(search_tasks(conn, "palette")) == ["421"]
    assert _ids(search_defects(conn, "palette")) == ["277", "276"]

    (defect,) = [s.doc for s in search_defects(conn, "unreachable")]
    assert defect.title == "Show all is unreachable"
    assert defect.href == "/defects/276"


def test_global_fan_out_leads_with_the_entity_kinds(conn):
    _seed_mixed(conn)
    payload = search(conn, "palette", None, [], scope=SCOPE_GLOBAL)
    assert [g["kind"] for g in payload["groups"]] == list(KIND_ORDER)
    assert payload["failed"] == []
    features, tasks = payload["groups"][:2]
    assert [i["id"] for i in features["items"]] == ["146", "147"]
    assert tasks["items"][0]["href"] == "/tasks/421"


def test_closed_entities_rank_last(conn):
    # The shipped feature says "palette" in its title AND body, outscoring the
    # open one on every count — and still sorts after it.
    _feature(conn, 1, "palette v1", body="palette palette palette", event=SHIPPED)
    _feature(conn, 2, "Navigator", body="the palette")
    _feature(conn, 3, "palette abandoned", event=ABANDONED)
    _task(conn, 10, "palette done", status="done")
    _task(conn, 11, "palette archived", status="archived")
    _task(conn, 12, "build", body="palette", status="running")
    _defect(conn, 20, "palette closed", status="closed")
    _defect(conn, 21, "palette declined", triage="declined")
    _defect(conn, 22, "row cut", missing="palette")
    conn.commit()

    assert _ids(search_features(conn, "palette")) == ["2", "1", "3"]
    assert _ids(search_tasks(conn, "palette"))[0] == "12"
    assert _ids(search_defects(conn, "palette"))[0] == "22"


def test_fan_out_caps_entity_groups_but_a_drill_pages(conn):
    for n in range(1, 8):
        _task(conn, n, f"palette step {n}")
    conn.commit()

    fan = search(conn, "palette", None, [], limit=6, scope=SCOPE_GLOBAL)
    (tasks,) = [g for g in fan["groups"] if g["kind"] == "task"]
    assert len(tasks["items"]) == ENTITY_FANOUT_CAP
    assert tasks["total"] == 7

    (drill,) = search(conn, "palette", ["task"], [], limit=12,
                      scope=SCOPE_GLOBAL)["groups"]
    assert len(drill["items"]) == 7


def test_session_scope_leaves_entities_out_unless_asked(conn):
    _feature(conn, 146, "palette")
    conn.commit()
    kinds = [g["kind"] for g in search(conn, "palette", None, ["t1"],
                                       scope=SCOPE_SESSION)["groups"]]
    assert not set(kinds) & set(ENTITY_KINDS)
    (group,) = search(conn, "palette", ["feature"], ["t1"])["groups"]
    assert group["total"] == 1


def test_like_wildcards_in_the_query_match_literally(conn):
    _task(conn, 1, "100% done")
    _task(conn, 2, "1000 rows")
    conn.commit()
    assert _ids(search_tasks(conn, "0%")) == ["1"]


def test_endpoint_attaches_rows_to_entity_items(conn, flask_client):
    _feature(conn, 146, "Search palette")
    conn.commit()
    body = flask_client.get(
        "/api/search?q=palette&kinds=feature&scope=global").get_json()
    (group,) = body["groups"]
    assert group["items"][0]["row"]["ref"] == "F146"


def test_total_is_exact_past_any_scan_bound(conn):
    conn.executemany(
        "INSERT INTO agent_tasks (id, title, status, source) "
        "VALUES (?, ?, 'idea', 'native')",
        [(n, f"qx step {n}") for n in range(1, 451)])
    conn.commit()
    (group,) = search(conn, "qx", ["task"], [], limit=25, scope=SCOPE_GLOBAL,
                      offset=440)["groups"]
    assert group["total"] == 450
    assert [i["id"] for i in group["items"]][-1] == "1"
