"""The feature ledger over HTTP — reads, writes, and who may do which.

Two rules run through every case here. **The read is local** — and now
completely so: the stage is the feature's last event plus a count of escapes,
so there is no tracker on this surface at all, and the `stage: null` an
unreachable one used to produce has nothing left to describe. And **nothing
varies by role in the payload**: regin's role split is about write-accidents,
not read-secrets, so a figure is never withheld — which is what makes a gap
counter unambiguous, since no number is ever missing for a second reason.
"""

from __future__ import annotations

import pytest

from lib.auth import create_token
from lib.features import attribution, store as feature_store


def _headers(role: str) -> dict:
    return {"Authorization": f"Bearer {create_token(1, role, role)}"}


@pytest.fixture
def a_feature(session_row, task_lane):
    feature = feature_store.create("the thing")
    session_row("t-web-1", cost=12.5, title="built the thing")
    task_lane(701, feature.id, [("t-web-1", "done")])
    return feature.id


# ── reads ────────────────────────────────────────────────────

def test_a_viewer_may_read_the_index(flask_client, tmp_db, a_feature):
    resp = flask_client.get("/api/features", headers=_headers("viewer"))

    assert resp.status_code == 200
    assert a_feature in {row["id"] for row in resp.get_json()["items"]}


def test_a_viewer_may_read_a_ledger(flask_client, tmp_db, a_feature):
    resp = flask_client.get(f"/api/features/{a_feature}",
                            headers=_headers("viewer"))

    assert resp.status_code == 200
    assert resp.get_json()["id"] == a_feature


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


def test_a_feature_always_has_a_stage(flask_client, tmp_db, a_feature,
                                      no_tracker):
    """It used to read `null` until something fetched the tracker, and 31 keys
    in the live database did. Every feature carries its own `specced`."""
    resp = flask_client.get(f"/api/features/{a_feature}",
                            headers=_headers("viewer"))

    assert resp.status_code == 200
    assert resp.get_json()["stage"]["stage"] == "spec"


def test_the_ledger_endpoint_never_resolves_a_tracker_reference(
        flask_client, tmp_db, a_feature, monkeypatch):
    from lib.tasks.integrations import registry
    monkeypatch.setattr(registry, "resolve_ref", _forbidden)

    assert flask_client.get(f"/api/features/{a_feature}",
                            headers=_headers("viewer")).status_code == 200


def test_the_ledger_endpoint_never_calls_fix_states(
        flask_client, tmp_db, a_feature, monkeypatch):
    from lib.defects import store
    monkeypatch.setattr(store, "fix_states", _forbidden)

    assert flask_client.get(f"/api/features/{a_feature}",
                            headers=_headers("viewer")).status_code == 200


def test_the_index_endpoint_never_resolves_a_tracker_reference(
        flask_client, tmp_db, a_feature, monkeypatch):
    from lib.tasks.integrations import registry
    monkeypatch.setattr(registry, "resolve_ref", _forbidden)

    assert flask_client.get("/api/features",
                            headers=_headers("viewer")).status_code == 200


def test_the_index_carries_its_header_counters(flask_client, tmp_db,
                                               a_feature):
    body = flask_client.get("/api/features",
                            headers=_headers("viewer")).get_json()

    assert set(body) == {"items", "pagination", "population", "applied",
                         "extras"}
    assert set(body["extras"]) == {"coverage", "unattributed_defects"}


def test_the_index_narrows_by_every_facet_it_declares(flask_client, tmp_db,
                                                      a_feature):
    """`q`, `stage` and `project_id` are filters, not decoration — each one
    has to narrow the SAME list, or a control on the page means nothing."""
    narrowed = flask_client.get("/api/features?q=the thing&stage=spec",
                                headers=_headers("viewer")).get_json()

    assert [row["id"] for row in narrowed["items"]] == [a_feature]
    assert narrowed["population"] >= 1


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
    assert [(r["feature_id"], r["provenance"], r["task_id"])
            for r in rows] == [(a_feature, "run", 701)]


def test_the_session_attribution_read_is_admin_only(flask_client, tmp_db,
                                                    a_feature):
    """The trace surface exposes every user's transcripts and carries no
    per-user owner, so it is admin-only — and this route hangs off it."""
    assert flask_client.get("/api/sessions/t-web-1/features",
                            headers=_headers("editor")).status_code == 403


# ── writes ───────────────────────────────────────────────────

def test_declaring_from_the_browser_is_recalled(flask_client, tmp_db,
                                                session_row, a_feature):
    session_row("t-web-2")

    resp = flask_client.post(f"/api/features/{a_feature}/attribute",
                             json={"trace_id": "t-web-2"},
                             headers=_headers("editor"))

    assert resp.status_code == 201
    row = attribution.features_for_session("t-web-2")[0]
    assert row.provenance == attribution.RECALLED
    assert row.round is None


def test_a_viewer_may_not_declare(flask_client, tmp_db, session_row,
                                  a_feature):
    session_row("t-web-3")

    resp = flask_client.post(f"/api/features/{a_feature}/attribute",
                             json={"trace_id": "t-web-3"},
                             headers=_headers("viewer"))

    assert resp.status_code == 403


def test_declaring_refuses_a_trace_regin_never_captured(flask_client, tmp_db,
                                                        a_feature):
    """`attribute()` validates only non-emptiness, so a typo would otherwise
    write a row for a session that does not exist."""
    resp = flask_client.post(f"/api/features/{a_feature}/attribute",
                             json={"trace_id": "t-nope"},
                             headers=_headers("editor"))

    assert resp.status_code == 404
    assert "t-nope" in resp.get_json()["error"]


def test_is_primary_defaults_false_and_is_never_a_side_effect(
        flask_client, tmp_db, session_row):
    """The browser keeps the CLI's contract exactly: first-writer-wins, and an
    unasked-for second declaration never moves a whole session's cost between
    two features."""
    first = feature_store.create("A").id
    second = feature_store.create("B").id
    session_row("t-web-4")
    attribution.attribute(first, "t-web-4")

    flask_client.post(f"/api/features/{second}/attribute",
                      json={"trace_id": "t-web-4"}, headers=_headers("editor"))

    rows = {r.feature_id: r.is_primary
            for r in attribution.features_for_session("t-web-4")}
    assert rows == {first: 1, second: 0}


def test_is_primary_demotes_the_incumbent_only_when_asked_for(
        flask_client, tmp_db, session_row):
    first = feature_store.create("A").id
    second = feature_store.create("B").id
    session_row("t-web-5")
    attribution.attribute(first, "t-web-5")

    flask_client.post(f"/api/features/{second}/attribute",
                      json={"trace_id": "t-web-5", "is_primary": True},
                      headers=_headers("editor"))

    rows = {r.feature_id: r.is_primary
            for r in attribution.features_for_session("t-web-5")}
    assert rows == {first: 0, second: 1}


def test_un_declaring_removes_the_row(flask_client, tmp_db, session_row,
                                      a_feature):
    session_row("t-web-6")
    attribution.attribute(a_feature, "t-web-6")

    resp = flask_client.delete(f"/api/features/{a_feature}/attribute/t-web-6",
                               headers=_headers("editor"))

    assert resp.status_code == 200
    assert attribution.features_for_session("t-web-6") == []


def test_un_declaring_what_has_no_row_is_a_named_404(flask_client, tmp_db,
                                                     a_feature):
    """A run-lane attribution has nothing to delete: the assertion is the
    task's placement under the feature."""
    resp = flask_client.delete(f"/api/features/{a_feature}/attribute/t-web-1",
                               headers=_headers("editor"))

    assert resp.status_code == 404
    assert "run" in resp.get_json()["error"].lower()


def test_a_viewer_may_not_un_declare(flask_client, tmp_db, session_row,
                                     a_feature):
    session_row("t-web-7")
    attribution.attribute(a_feature, "t-web-7")

    assert flask_client.delete(
        f"/api/features/{a_feature}/attribute/t-web-7",
        headers=_headers("viewer")).status_code == 403


def test_a_viewer_may_not_refresh_a_remote_feature(flask_client, tmp_db):
    """Read-shaped, but it spends someone else's quota and appends to an
    append-only table — the same split `tasks.py` already draws."""
    feature = feature_store.create("remote", origin="linear",
                                   external_ref="CAI-800")

    assert flask_client.post(f"/api/features/{feature.id}/refresh",
                             headers=_headers("viewer")).status_code == 403


def test_refreshing_a_native_feature_is_refused_by_name(flask_client, tmp_db,
                                                        a_feature):
    """There is nothing to fetch: its stage is derived in this database, which
    is why the whole population no longer waits on a tracker to have one."""
    resp = flask_client.post(f"/api/features/{a_feature}/refresh",
                             headers=_headers("editor"))

    assert resp.status_code == 400
    assert "native" in resp.get_json()["error"]


def test_a_failed_refresh_appends_no_event_and_says_the_stage_is_unchanged(
        flask_client, tmp_db, no_tracker):
    from lib.features import events

    feature = feature_store.create("remote", origin="linear",
                                   external_ref="CAI-801")

    resp = flask_client.post(f"/api/features/{feature.id}/refresh",
                             headers=_headers("editor"))

    assert resp.status_code == 502
    assert resp.get_json()["stage"]["stage"] == "spec"
    assert [e.kind for e in events.history(feature.id)] == ["specced"]
    assert feature_store.require(feature.id).synced_at is None


def test_a_refresh_folds_the_remote_state_into_the_log(flask_client, tmp_db,
                                                       tracker_issue):
    from lib.features import events

    feature = feature_store.create("remote", origin="linear",
                                   external_ref="CAI-802")
    tracker_issue("CAI-802", state_category="completed", title="Refreshed")

    resp = flask_client.post(f"/api/features/{feature.id}/refresh",
                             headers=_headers("editor"))

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["stage"]["stage"] == "shipped"
    assert events.last(feature.id).declared_by == "sync:linear"
    assert feature_store.require(feature.id).title == "Refreshed"


def _forbidden(*args, **kwargs):
    raise AssertionError("a web read must not reach the tracker")
