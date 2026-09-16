"""The feature ledger over HTTP — reads, writes, and who may do which.

Two rules run through every case here. **The read is local** (CAI-245): an
unreachable tracker is a 200 with `stage: null`, never a 5xx and never a
spinner that resolves into a fetch. And **nothing varies by role in the
payload** (CAI-246): regin's role split is about write-accidents, not
read-secrets, so a figure is never withheld — which is what makes a gap
counter unambiguous, since no number is ever missing for a second reason.
"""

from __future__ import annotations

import pytest

from lib.auth import create_token
from lib.features import attribution


def _headers(role: str) -> dict:
    return {"Authorization": f"Bearer {create_token(1, role, role)}"}


@pytest.fixture
def a_feature(session_row, task_lane):
    session_row("t-web-1", cost=12.5, title="built the thing")
    task_lane(701, ["CAI-600"], [("t-web-1", "done")])
    return "CAI-600"


# ── reads ────────────────────────────────────────────────────

def test_a_viewer_may_read_the_index(flask_client, tmp_db, a_feature):
    resp = flask_client.get("/api/features", headers=_headers("viewer"))

    assert resp.status_code == 200
    assert a_feature in {row["key"] for row in resp.get_json()["items"]}


def test_a_viewer_may_read_a_ledger(flask_client, tmp_db, a_feature):
    resp = flask_client.get(f"/api/features/{a_feature}",
                            headers=_headers("viewer"))

    assert resp.status_code == 200
    assert resp.get_json()["key"] == a_feature


def test_an_anonymous_reader_is_refused(anon_client, tmp_db, a_feature):
    assert anon_client.get("/api/features").status_code == 401


def test_the_payload_is_identical_for_every_role(flask_client, tmp_db,
                                                 a_feature):
    """Field-level redaction has no precedent in regin, and a withheld figure
    could not be told apart from a gap."""
    bodies = [flask_client.get(f"/api/features/{a_feature}",
                               headers=_headers(role)).get_json()
              for role in ("viewer", "editor", "admin")]

    assert bodies[0] == bodies[1] == bodies[2]


def test_no_payload_carries_a_can_open_field(flask_client, tmp_db, a_feature):
    """The trace link is a component decision from the stored role. A
    `can_open` field would smuggle role-varying payloads back in."""
    body = flask_client.get(f"/api/features/{a_feature}",
                            headers=_headers("viewer")).get_json()

    assert "can_open" not in body
    assert all("can_open" not in row for row in body["sessions"]["rows"])


def test_the_session_rows_are_never_omitted(flask_client, tmp_db, a_feature):
    """The cost is a sum over them, and an absent row is indistinguishable
    from the gap counter that means a counted absence."""
    body = flask_client.get(f"/api/features/{a_feature}",
                            headers=_headers("viewer")).get_json()

    assert [row["trace_id"] for row in body["sessions"]["rows"]] == ["t-web-1"]


def test_an_unreachable_tracker_reads_200_with_a_null_stage(
        flask_client, tmp_db, a_feature, no_tracker):
    resp = flask_client.get(f"/api/features/{a_feature}",
                            headers=_headers("viewer"))

    assert resp.status_code == 200
    assert resp.get_json()["stage"] is None


def test_the_ledger_endpoint_never_calls_fetch_issue(
        flask_client, tmp_db, a_feature, monkeypatch):
    import lib.defects.tracker as tracker_mod
    monkeypatch.setattr(tracker_mod, "resolve", _forbidden)

    assert flask_client.get(f"/api/features/{a_feature}",
                            headers=_headers("viewer")).status_code == 200


def test_the_ledger_endpoint_never_calls_fix_states(
        flask_client, tmp_db, a_feature, monkeypatch):
    from lib.defects import store
    monkeypatch.setattr(store, "fix_states", _forbidden)

    assert flask_client.get(f"/api/features/{a_feature}",
                            headers=_headers("viewer")).status_code == 200


def test_the_index_endpoint_never_calls_fetch_issue(
        flask_client, tmp_db, a_feature, monkeypatch):
    import lib.defects.tracker as tracker_mod
    monkeypatch.setattr(tracker_mod, "resolve", _forbidden)

    assert flask_client.get("/api/features",
                            headers=_headers("viewer")).status_code == 200


def test_the_index_carries_its_header_counters(flask_client, tmp_db,
                                               a_feature):
    body = flask_client.get("/api/features",
                            headers=_headers("viewer")).get_json()

    assert set(body) == {"items", "total", "coverage", "unattributed_defects"}


def test_the_index_takes_exactly_one_query_parameter(flask_client, tmp_db,
                                                     a_feature):
    """No cursor: the population grows only by operator acts, so it grows at
    the rate a human can scan."""
    narrowed = flask_client.get("/api/features?q=CAI-600&cursor=x&stage=spec",
                                headers=_headers("viewer")).get_json()

    assert [row["key"] for row in narrowed["items"]] == ["CAI-600"]
    assert narrowed["total"] >= 1


def test_a_q_longer_than_the_cap_is_truncated_not_refused(flask_client,
                                                          tmp_db, a_feature):
    resp = flask_client.get(f"/api/features?q={'x' * 500}",
                            headers=_headers("viewer"))

    assert resp.status_code == 200
    assert resp.get_json()["items"] == []


def test_a_session_reads_its_own_attribution(flask_client, tmp_db, a_feature):
    resp = flask_client.get("/api/sessions/t-web-1/features",
                            headers=_headers("admin"))

    assert resp.status_code == 200
    rows = resp.get_json()["features"]
    assert [(r["feature_key"], r["provenance"], r["task_id"])
            for r in rows] == [("CAI-600", "task-link", 701)]


def test_the_session_attribution_read_is_admin_only(flask_client, tmp_db,
                                                    a_feature):
    """The trace surface exposes every user's transcripts and carries no
    per-user owner, so it is admin-only — and this route hangs off it."""
    assert flask_client.get("/api/sessions/t-web-1/features",
                            headers=_headers("editor")).status_code == 403


# ── writes ───────────────────────────────────────────────────

def test_declaring_from_the_browser_is_recalled(flask_client, tmp_db,
                                                session_row, no_tracker):
    session_row("t-web-2")

    resp = flask_client.post("/api/features/CAI-601/attribute",
                             json={"trace_id": "t-web-2"},
                             headers=_headers("editor"))

    assert resp.status_code == 201
    row = attribution.features_for_session("t-web-2")[0]
    assert row.provenance == attribution.RECALLED
    assert row.round is None


def test_a_viewer_may_not_declare(flask_client, tmp_db, session_row):
    session_row("t-web-3")

    resp = flask_client.post("/api/features/CAI-602/attribute",
                             json={"trace_id": "t-web-3"},
                             headers=_headers("viewer"))

    assert resp.status_code == 403


def test_declaring_refuses_a_trace_regin_never_captured(flask_client, tmp_db):
    """`attribute()` validates only non-emptiness, so a typo would otherwise
    write a row for a session that does not exist."""
    resp = flask_client.post("/api/features/CAI-603/attribute",
                             json={"trace_id": "t-nope"},
                             headers=_headers("editor"))

    assert resp.status_code == 404
    assert "t-nope" in resp.get_json()["error"]


def test_is_primary_defaults_false_and_is_never_a_side_effect(
        flask_client, tmp_db, session_row, no_tracker):
    """The browser keeps the CLI's contract exactly: first-writer-wins, and an
    unasked-for second declaration never moves a whole session's cost between
    two features."""
    session_row("t-web-4")
    attribution.attribute("CAI-604", "t-web-4")

    flask_client.post("/api/features/CAI-614/attribute",
                      json={"trace_id": "t-web-4"}, headers=_headers("editor"))

    rows = {r.feature_key: r.is_primary
            for r in attribution.features_for_session("t-web-4")}
    assert rows == {"CAI-604": 1, "CAI-614": 0}


def test_is_primary_demotes_the_incumbent_only_when_asked_for(
        flask_client, tmp_db, session_row, no_tracker):
    session_row("t-web-5")
    attribution.attribute("CAI-605", "t-web-5")

    flask_client.post("/api/features/CAI-615/attribute",
                      json={"trace_id": "t-web-5", "is_primary": True},
                      headers=_headers("editor"))

    rows = {r.feature_key: r.is_primary
            for r in attribution.features_for_session("t-web-5")}
    assert rows == {"CAI-605": 0, "CAI-615": 1}


def test_un_declaring_removes_the_row(flask_client, tmp_db, session_row,
                                      no_tracker):
    session_row("t-web-6")
    attribution.attribute("CAI-606", "t-web-6")

    resp = flask_client.delete("/api/features/CAI-606/attribute/t-web-6",
                               headers=_headers("editor"))

    assert resp.status_code == 200
    assert attribution.features_for_session("t-web-6") == []


def test_un_declaring_what_has_no_row_is_a_named_404(flask_client, tmp_db,
                                                     a_feature):
    """A task-lane attribution has nothing to delete: the assertion is the
    link on the task."""
    resp = flask_client.delete(f"/api/features/{a_feature}/attribute/t-web-1",
                               headers=_headers("editor"))

    assert resp.status_code == 404
    assert "task" in resp.get_json()["error"].lower()


def test_a_viewer_may_not_un_declare(flask_client, tmp_db, session_row,
                                     no_tracker):
    session_row("t-web-7")
    attribution.attribute("CAI-607", "t-web-7")

    assert flask_client.delete("/api/features/CAI-607/attribute/t-web-7",
                               headers=_headers("viewer")).status_code == 403


def test_a_viewer_may_not_refresh_the_stage(flask_client, tmp_db, a_feature):
    """Read-shaped, but it writes the shared cache and spends someone else's
    quota — the same split `tasks.py` already draws."""
    assert flask_client.post(f"/api/features/{a_feature}/stage/refresh",
                             headers=_headers("viewer")).status_code == 403


def test_a_failed_refresh_persists_nothing(flask_client, tmp_db, a_feature,
                                           no_tracker):
    from lib.features import stage_source

    resp = flask_client.post(f"/api/features/{a_feature}/stage/refresh",
                             headers=_headers("editor"))

    assert resp.status_code == 502
    assert resp.get_json()["stage"] is None
    assert stage_source.synced_at(a_feature) is None


def test_a_refresh_writes_the_shared_cache(flask_client, tmp_db, a_feature,
                                           tracker_issue):
    from lib.features import stage_source
    tracker_issue(a_feature, state_category="completed", title="Refreshed")

    resp = flask_client.post(f"/api/features/{a_feature}/stage/refresh",
                             headers=_headers("editor"))

    assert resp.status_code == 200
    assert resp.get_json()["stage"]["stage"] == "shipped"
    assert stage_source.snapshot(a_feature)["title"] == "Refreshed"


def _forbidden(*args, **kwargs):
    raise AssertionError("a web read must not reach the tracker")
