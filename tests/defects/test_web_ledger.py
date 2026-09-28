"""The defect ledger's reads and writes on the web (regin:81).

`POST /api/defects` shipped the open verb alone, which left the ledger
write-only on the browser: a defect's text was readable only through
`regin defects list`, so the `/features` unattributed counter pointed at a
remedy no page offered. These are the routes that earn the list.

Every refusal asserted here is the store's, not the route's — the blueprint
holds no `SessionLocal()` and adds no rule, it only maps `DefectError` onto a
status code. Each test is one Gherkin scenario from the feature's acceptance
list.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from lib.auth import create_token
from lib.defects import store
from lib.orm import SessionLocal
from lib.orm.models.defects import Defect

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}

#: Exactly the `DefectView` shape. Asserted as a set so a field nobody
#: designed — a suggested feature key above all — cannot arrive unnoticed.
VIEW_FIELDS = {
    "id", "feature_id", "feature_title", "symptom", "status", "round",
    "blamed_event_kind", "missing_check", "suspect_paths", "fix_task_id",
    "fix_state", "triage_state", "triage_reason",
    "opened_at", "updated_at", "closed_at", "images",
}


def _row(defect_id: int) -> Defect:
    with SessionLocal() as session:
        return session.get(Defect, defect_id)


def _all_rows():
    with SessionLocal() as session:
        return session.exec(select(Defect)).all()


# ── 1.1 / 1.2 — the list ──────────────────────────────────────


def test_the_unattributed_list_is_readable_from_the_browser(flask_client):
    """1.1 — every unattributed defect, with its full symptom, status, round."""
    long_symptom = "the gutter folds the newest span " * 6
    opened = [store.record(long_symptom + str(n), session_id=f"t-{n}")
              for n in range(8)]
    store.close(opened[2].id, missing_check="a test")

    resp = flask_client.get("/api/defects?scope=unattributed")

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["applied"]["filters"]["scope"] == "unattributed"
    # Newest first, the same direction every other scope reads in. This used
    # to be `id ASC` while `all` was `id DESC`.
    assert [item["id"] for item in body["items"]] == \
        [r.id for r in reversed(opened)]
    first = body["items"][-1]
    assert set(first) == VIEW_FIELDS
    assert first["symptom"] == opened[0].symptom  # verbatim, never truncated
    assert first["status"] == "open" and first["round"] == 1
    assert [i["status"] for i in reversed(body["items"])][2] == "closed"


def test_the_counter_and_the_list_report_the_same_number(flask_client):
    """1.2 — `/features`' counter and the triage rail's `/defects` list agree,
    and neither counts a closed report: it has nothing left to route."""
    for n in range(3):
        store.record(f"symptom {n}", session_id=f"t-{n}")
    fixed = store.record("long since fixed", session_id="t-closed")
    store.close(fixed.id, missing_check="a test")

    counter = flask_client.get("/api/features").get_json()
    listed = flask_client.get(
        "/api/defects?scope=unattributed&status=open").get_json()

    assert counter["extras"]["unattributed_defects"] == len(listed["items"]) == 3
    assert fixed.id not in {item["id"] for item in listed["items"]}


def test_a_feature_scope_lists_that_features_defects(flask_client,
                                                     feature_row):
    feature = feature_row()
    mine = store.record("mine broke", feature_id=feature.id)
    store.record("something else broke", session_id="t-1")

    body = flask_client.get(f"/api/defects?feature_id={feature.id}").get_json()

    assert body["applied"]["filters"]["scope"] == "feature"
    assert [i["id"] for i in body["items"]] == [mine.id]


def test_a_fix_item_scope_lists_every_defect_one_task_should_close(
        flask_client, native_task):
    task_id = native_task(title="the fix")
    first = store.record("a", session_id="t-1")
    second = store.record("b", session_id="t-2")
    for row in (first, second):
        store.set_fix_item(row.id, task_id)

    body = flask_client.get(f"/api/defects?fix_item={task_id}").get_json()

    assert body["applied"]["filters"]["scope"] == "fix-item"
    assert [i["id"] for i in body["items"]] == [second.id, first.id]
    assert body["items"][0]["fix_task_id"] == task_id


def test_a_fix_item_that_is_not_an_id_names_no_scope(flask_client):
    """A malformed id is the same answer as an absent one — both mean "no such
    scope was asked for". A 400 would make a typo louder than a wrong id."""
    body = flask_client.get("/api/defects?fix_item=nonsense").get_json()

    assert body["applied"]["filters"]["scope"] == "all"


def test_the_default_scope_is_the_whole_ledger(flask_client, feature_row):
    """The repair: a defect attributed at the moment it was reported used to
    belong to no scope any surface opened, so the page that filed it said
    saved and then could not show it."""
    feature = feature_row()
    blamed = store.record("filed from the task page", feature_id=feature.id)
    loose = store.record("filed by nobody", session_id="t-1")

    body = flask_client.get("/api/defects").get_json()

    assert body["applied"]["filters"]["scope"] == "all"
    assert {i["id"] for i in body["items"]} == {blamed.id, loose.id}
    assert [i["id"] for i in body["items"]] == [loose.id, blamed.id]


def test_the_two_ledger_wide_scopes_partition_the_table(flask_client,
                                                        feature_row):
    feature = feature_row()
    store.record("blamed", feature_id=feature.id)
    store.record("unblamed", session_id="t-1")

    every = flask_client.get("/api/defects").get_json()["items"]
    loose = flask_client.get(
        "/api/defects?scope=unattributed").get_json()["items"]
    blamed = flask_client.get(
        f"/api/defects?feature_id={feature.id}").get_json()["items"]

    assert len(every) == len(loose) + len(blamed)
    assert {i["id"] for i in every} == {i["id"] for i in loose + blamed}


def test_an_unknown_scope_is_a_bad_request(flask_client):
    assert flask_client.get("/api/defects?scope=closed").status_code == 400


def test_anonymous_cannot_read_the_ledger(anon_client):
    assert anon_client.get("/api/defects").status_code == 401


# ── 2.1 / 2.2 — attribution ───────────────────────────────────


def test_the_list_never_suggests_a_feature_key(flask_client):
    """2.1 — an unattributed row carries its paths and no proposed parent."""
    store.record("cost is inverted", session_id="t-1",
                 suspect_paths=["lib/features/cost.py"])

    item = flask_client.get("/api/defects").get_json()["items"][0]

    assert item["feature_id"] is None
    assert item["suspect_paths"] == ["lib/features/cost.py"]
    assert set(item) == VIEW_FIELDS


def test_attributing_binds_the_feature_and_points_at_its_last_event(
        flask_client, feature_row):
    feature = feature_row()
    row = store.record("it broke", session_id="t-1")

    resp = flask_client.post(f"/api/defects/{row.id}/attribute",
                             json={"feature_id": feature.id}, headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["defect"]["feature_id"] == feature.id
    assert resp.get_json()["defect"]["blamed_event_kind"] == "specced"
    assert _row(row.id).feature_id == feature.id


def test_attribution_with_no_feature_is_refused(flask_client):
    row = store.record("it broke", session_id="t-1")

    resp = flask_client.post(f"/api/defects/{row.id}/attribute",
                             json={}, headers=EDITOR)

    assert resp.status_code == 400
    assert "feature id" in resp.get_json()["error"]
    assert _row(row.id).feature_id is None


def test_attributing_a_symptom_the_feature_already_reports_is_refused(
        flask_client, feature_row):
    """The collision names `merge` and absorbs nothing."""
    feature = feature_row()
    store.record("it broke", feature_id=feature.id)
    mine = store.record("It   Broke ", session_id="t-1")

    resp = flask_client.post(f"/api/defects/{mine.id}/attribute",
                             json={"feature_id": feature.id}, headers=EDITOR)

    assert resp.status_code == 409
    assert "merge" in resp.get_json()["error"]
    assert _row(mine.id).feature_id is None


def test_attributing_an_unknown_defect_is_a_404(flask_client, feature_row):
    resp = flask_client.post("/api/defects/9999/attribute",
                             json={"feature_id": feature_row().id},
                             headers=EDITOR)
    assert resp.status_code == 404


# ── 3.1 / 3.2 — close and reopen ──────────────────────────────


def test_closing_names_the_check_that_would_have_caught_it(flask_client):
    row = store.record("it broke", session_id="t-1")

    resp = flask_client.post(f"/api/defects/{row.id}/close",
                             json={"missing_check": " a route test "},
                             headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()["defect"]
    assert body["status"] == "closed" and body["missing_check"] == "a route test"
    assert _row(row.id).missing_check == "a route test"


def test_closing_with_an_empty_check_is_refused(flask_client):
    """3.1 — the check is the only output that changes future behaviour."""
    row = store.record("it broke", session_id="t-1")

    resp = flask_client.post(f"/api/defects/{row.id}/close",
                             json={"missing_check": "  "}, headers=EDITOR)

    assert resp.status_code == 400
    assert _row(row.id).status == "open"
    assert _row(row.id).missing_check is None


def test_reopening_a_closed_defect_moves_the_round_and_clears_the_check(
        flask_client):
    row = store.record("it broke", session_id="t-1")
    store.close(row.id, missing_check="a route test")

    resp = flask_client.post(f"/api/defects/{row.id}/reopen", headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()["defect"]
    assert body["status"] == "open" and body["round"] == 2
    assert body["missing_check"] is None and body["closed_at"] is None


def test_reopening_an_open_defect_does_not_move_the_round(flask_client):
    """3.2 — a round counts fix attempts walked back, not ones never made."""
    row = store.record("it broke", session_id="t-1")

    resp = flask_client.post(f"/api/defects/{row.id}/reopen", headers=EDITOR)

    assert resp.status_code == 409
    assert _row(row.id).round == 1
    assert _row(row.id).status == "open"


def test_closing_or_reopening_an_unknown_defect_is_a_404(flask_client):
    assert flask_client.post("/api/defects/9999/close",
                             json={"missing_check": "x"},
                             headers=EDITOR).status_code == 404
    assert flask_client.post("/api/defects/9999/reopen",
                             headers=EDITOR).status_code == 404


# ── 4.1 — the guard ───────────────────────────────────────────


def _write_acts(defect_id: int, feature_id):
    return ((f"/api/defects/{defect_id}/attribute",
             {"feature_id": feature_id}),
            (f"/api/defects/{defect_id}/close", {"missing_check": "x"}),
            (f"/api/defects/{defect_id}/reopen", {}))


def test_a_viewer_cannot_attribute_close_or_reopen(flask_client, feature_row):
    """Every write takes `require_editor`, and none of them lands."""
    feature = feature_row()
    row = store.record("it broke", session_id="t-1")
    store.close(row.id, missing_check="a route test")
    before = len(_all_rows())

    for path, payload in _write_acts(row.id, feature.id):
        assert flask_client.post(path, json=payload,
                                 headers=VIEWER).status_code == 403

    after = _row(row.id)
    assert len(_all_rows()) == before
    assert after.feature_id is None
    assert after.status == "closed" and after.round == 1


def test_anonymous_cannot_attribute_close_or_reopen(anon_client):
    for path, payload in _write_acts(1, 1):
        assert anon_client.post(path, json=payload).status_code == 401


# ── identity is an integer, so there is nothing to normalize ───


def test_a_feature_that_does_not_exist_cannot_be_blamed(flask_client):
    """The padding and blank-string rules this replaced existed because the
    key was a free string: `' CAI-206'` was a SECOND feature in the population
    and `''` belonged to no scope at all. A foreign key admits neither."""
    row = store.record("it broke", session_id="t-1")

    resp = flask_client.post(f"/api/defects/{row.id}/attribute",
                             json={"feature_id": 9999}, headers=EDITOR)

    assert resp.status_code == 409
    assert _row(row.id).feature_id is None


def test_the_symptom_cap_the_model_declares_is_enforced(flask_client):
    """`SYMPTOM_MAX` was declared and referenced nowhere — a claim about the
    data that the bare TEXT column did not make true."""
    with pytest.raises(store.DefectError, match="symptom"):
        store.record("x" * 4001, session_id="t-1")

    assert store.record("x" * 4000, session_id="t-1").id  # the cap itself fits


# ── the fix-item scope ─────


def test_a_fix_item_scope_is_found_by_the_task_it_names(flask_client,
                                                        native_task):
    """The pair this replaced stored the tracker's canonical key while the
    query read the raw text, so a pasted URL answered "nothing points at this
    item" for rows that did. An integer has one spelling."""
    task_id = native_task(title="the fix")
    row = store.record("one", session_id="t-1")
    store.set_fix_item(row.id, task_id)

    body = flask_client.get(f"/api/defects?fix_item={task_id}").get_json()

    assert [i["id"] for i in body["items"]] == [row.id]
