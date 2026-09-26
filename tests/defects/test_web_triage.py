"""The triage queue and its four terminal verbs (feature #94).

The queue is a *scope*, not a second reader: `?scope=triage` runs the same
statement every other defect list runs, so its `pagination.total` and its rows
come out of one session under one predicate. That is the whole of invariant 4
— the badge cannot disagree with the queue because there is nothing for it to
disagree with.

Each test is one acceptance item. The snooze tests are the ones that matter
most: they assert the return condition is an *event on the row*, never the
clock, by reading the queue an arbitrary number of times with nothing in
between and getting the same answer.
"""

from __future__ import annotations

from lib.auth import create_token
from lib.defects import store
from lib.orm import SessionLocal
from lib.orm.models.defects import Defect

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}


def _queue(client):
    body = client.get("/api/defects?scope=triage", headers=EDITOR).get_json()
    return body


def _row(defect_id: int) -> Defect:
    with SessionLocal() as session:
        return session.get(Defect, defect_id)


# ── the queue itself ──────────────────────────────────────────


def test_the_queue_holds_every_unrouted_unattributed_defect(flask_client):
    rows = [store.record(f"symptom {n}", session_id=f"t-{n}") for n in range(3)]
    body = _queue(flask_client)
    assert body["applied"]["filters"]["scope"] == "triage"
    assert {item["id"] for item in body["items"]} == {r.id for r in rows}
    assert body["pagination"]["total"] == 3


def test_the_queue_preselects_no_feature(flask_client, feature_row):
    """Acceptance 2 — nothing on a queued defect proposes its parent."""
    feature_row("the feature the suspect path belongs to")
    store.record("a symptom", session_id="t-1",
                 suspect_paths=["lib/features/cost.py"])
    item = _queue(flask_client)["items"][0]
    # The two fields that could name a parent, both empty — and no third
    # field under any spelling that could be read as a proposal.
    assert item["feature_id"] is None
    assert item["feature_title"] is None
    assert not [k for k in item
                if "suggest" in k or "propose" in k or "candidate" in k]


def test_an_attributed_defect_is_not_in_the_queue(flask_client, feature_row):
    feature_id = feature_row().id
    row = store.record("a symptom", session_id="t-1")
    store.attribute(row.id, feature_id)
    assert _queue(flask_client)["pagination"]["total"] == 0


def test_a_closed_defect_is_not_in_the_queue(flask_client):
    row = store.record("a symptom", session_id="t-1")
    store.close(row.id, missing_check="a test")
    assert _queue(flask_client)["pagination"]["total"] == 0


def test_the_count_and_the_rows_come_from_one_read(flask_client):
    """Invariant 4 — `total` describes the population the rows are drawn from,
    not the page, so a badge reading it cannot disagree with the queue."""
    for n in range(7):
        store.record(f"symptom {n}", session_id=f"t-{n}")
    body = flask_client.get("/api/defects?scope=triage&size=3",
                            headers=EDITOR).get_json()
    assert len(body["items"]) == 3
    assert body["pagination"]["total"] == 7


# ── verb 1: accept ────────────────────────────────────────────


def test_accepting_takes_one_off_the_queue(flask_client, feature_row):
    feature_id = feature_row().id
    rows = [store.record(f"symptom {n}", session_id=f"t-{n}") for n in range(3)]
    assert _queue(flask_client)["pagination"]["total"] == 3
    resp = flask_client.post(f"/api/defects/{rows[0].id}/attribute",
                             json={"feature_id": feature_id}, headers=EDITOR)
    assert resp.status_code == 200, resp.get_json()
    assert _queue(flask_client)["pagination"]["total"] == 2


def test_accepting_clears_a_snooze(flask_client, feature_row):
    """A defect that left by being blamed keeps no opinion about the queue."""
    feature_id = feature_row().id
    row = store.record("a symptom", session_id="t-1")
    store.snooze(row.id)
    store.attribute(row.id, feature_id)
    after = _row(row.id)
    assert after.triage_state is None and after.snooze_watermark is None


# ── verb 2: mark duplicate (merge) ────────────────────────────


def test_merging_from_the_browser_unions_the_suspect_paths(flask_client):
    """Acceptance 1 — the keeper carries the union of both path sets."""
    dup = store.record("the gutter folds", session_id="t-1",
                       suspect_paths=["a.py", "shared.py"])
    keeper = store.record("gutter folding again", session_id="t-2",
                          suspect_paths=["b.py", "shared.py"])
    resp = flask_client.post(f"/api/defects/{dup.id}/merge",
                             json={"into": keeper.id}, headers=EDITOR)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()["defect"]
    assert body["id"] == keeper.id
    assert sorted(body["suspect_paths"]) == ["a.py", "b.py", "shared.py"]
    assert _row(dup.id) is None
    assert _queue(flask_client)["pagination"]["total"] == 1


def test_merging_rewrites_neither_rows_blame(flask_client, feature_row):
    """Invariant 3 — a merge moves paths, never the escape verdict."""
    feature_id = feature_row().id
    keeper = store.record("keeper", session_id="t-1")
    store.attribute(keeper.id, feature_id)
    blamed = _row(keeper.id).blamed_event_id
    assert blamed is not None
    dup = store.record("dup", session_id="t-2", suspect_paths=["a.py"])
    flask_client.post(f"/api/defects/{dup.id}/merge",
                      json={"into": keeper.id}, headers=EDITOR)
    assert _row(keeper.id).blamed_event_id == blamed


def test_merging_a_defect_into_itself_is_refused(flask_client):
    row = store.record("a symptom", session_id="t-1")
    resp = flask_client.post(f"/api/defects/{row.id}/merge",
                             json={"into": row.id}, headers=EDITOR)
    assert resp.status_code == 409
    assert "itself" in resp.get_json()["error"]


def test_merging_names_the_missing_row(flask_client):
    row = store.record("a symptom", session_id="t-1")
    assert flask_client.post(f"/api/defects/{row.id}/merge",
                             json={"into": 9999},
                             headers=EDITOR).status_code == 404
    assert flask_client.post("/api/defects/9999/merge",
                             json={"into": row.id},
                             headers=EDITOR).status_code == 404


def test_merging_needs_a_keeper_that_is_a_row_id(flask_client):
    row = store.record("a symptom", session_id="t-1")
    for bad in ({}, {"into": True}, {"into": "7"}, {"into": 0}):
        resp = flask_client.post(f"/api/defects/{row.id}/merge", json=bad,
                                 headers=EDITOR)
        assert resp.status_code == 400, (bad, resp.get_json())


def test_a_viewer_cannot_merge(flask_client):
    a = store.record("a", session_id="t-1")
    b = store.record("b", session_id="t-2")
    assert flask_client.post(f"/api/defects/{a.id}/merge", json={"into": b.id},
                             headers=VIEWER).status_code == 403


# ── verb 3: decline ───────────────────────────────────────────


def test_declining_closes_without_naming_a_feature_or_a_check(flask_client):
    row = store.record("not actually a bug", session_id="t-1")
    resp = flask_client.post(f"/api/defects/{row.id}/decline",
                             json={"reason": "works as designed"},
                             headers=EDITOR)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()["defect"]
    assert body["status"] == "closed"
    assert body["feature_id"] is None
    assert body["missing_check"] is None
    assert body["triage_state"] == "declined"
    assert body["triage_reason"] == "works as designed"
    assert _queue(flask_client)["pagination"]["total"] == 0


def test_declining_takes_no_reason_at_all(flask_client):
    row = store.record("a symptom", session_id="t-1")
    resp = flask_client.post(f"/api/defects/{row.id}/decline", json={},
                             headers=EDITOR)
    assert resp.status_code == 200
    assert resp.get_json()["defect"]["triage_reason"] is None


def test_declining_an_already_closed_defect_is_refused(flask_client):
    row = store.record("a symptom", session_id="t-1")
    store.close(row.id, missing_check="a test")
    resp = flask_client.post(f"/api/defects/{row.id}/decline", json={},
                             headers=EDITOR)
    assert resp.status_code == 409


def test_reopening_a_declined_defect_returns_it_to_the_queue(flask_client):
    row = store.record("a symptom", session_id="t-1")
    flask_client.post(f"/api/defects/{row.id}/decline", json={},
                      headers=EDITOR)
    flask_client.post(f"/api/defects/{row.id}/reopen", headers=EDITOR)
    assert _queue(flask_client)["pagination"]["total"] == 1
    assert _row(row.id).triage_state is None


# ── verb H: snooze ────────────────────────────────────────────


def test_snoozing_leaves_the_queue_and_stays_out_with_no_activity(flask_client):
    """A snooze has no deadline: reading the queue does not end it."""
    row = store.record("a symptom", session_id="t-1")
    resp = flask_client.post(f"/api/defects/{row.id}/snooze", headers=EDITOR)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["defect"]["triage_state"] == "snoozed"
    for _ in range(5):
        assert _queue(flask_client)["pagination"]["total"] == 0


def test_activity_returns_a_snoozed_defect_to_the_queue(flask_client):
    row = store.record("a symptom", session_id="t-1")
    flask_client.post(f"/api/defects/{row.id}/snooze", headers=EDITOR)
    assert _queue(flask_client)["pagination"]["total"] == 0
    # A merge into it is activity: the keeper's `updated_at` moves, which is
    # exactly the event the watermark is compared against.
    other = store.record("another report of it", session_id="t-2",
                         suspect_paths=["a.py"])
    flask_client.post(f"/api/defects/{other.id}/merge", json={"into": row.id},
                      headers=EDITOR)
    assert _queue(flask_client)["pagination"]["total"] == 1


def test_re_snoozing_holds_it_out_again(flask_client):
    row = store.record("a symptom", session_id="t-1")
    store.snooze(row.id)
    other = store.record("dup", session_id="t-2", suspect_paths=["a.py"])
    store.merge(other.id, into=row.id)
    assert _queue(flask_client)["pagination"]["total"] == 1
    flask_client.post(f"/api/defects/{row.id}/snooze", headers=EDITOR)
    assert _queue(flask_client)["pagination"]["total"] == 0


def test_snoozing_does_not_count_as_activity_on_the_defect(flask_client):
    """Otherwise the snooze would end in the same breath that began it."""
    row = store.record("a symptom", session_id="t-1")
    before = _row(row.id).activity_seq
    store.snooze(row.id)
    after = _row(row.id)
    assert after.activity_seq == before
    assert after.snooze_watermark == before


def test_snoozing_an_attributed_or_closed_defect_is_refused(flask_client, feature_row):
    feature_id = feature_row().id
    attributed = store.record("a symptom", session_id="t-1")
    store.attribute(attributed.id, feature_id)
    assert flask_client.post(f"/api/defects/{attributed.id}/snooze",
                             headers=EDITOR).status_code == 409
    closed = store.record("another symptom", session_id="t-2")
    store.close(closed.id, missing_check="a test")
    assert flask_client.post(f"/api/defects/{closed.id}/snooze",
                             headers=EDITOR).status_code == 409


# ── the CLI's other orphan: fix state ─────────────────────────


def test_the_list_carries_fix_state_only_when_asked(flask_client, native_task):
    task_id = native_task(status="running")
    row = store.record("a symptom", session_id="t-1")
    store.set_fix_item(row.id, task_id)
    plain = flask_client.get("/api/defects", headers=EDITOR).get_json()
    assert plain["items"][0]["fix_state"] is None
    asked = flask_client.get("/api/defects?with_fix_state=1",
                             headers=EDITOR).get_json()
    assert asked["items"][0]["fix_state"] == "running"


def test_fix_state_is_none_for_a_defect_pointing_at_nothing(flask_client):
    store.record("a symptom", session_id="t-1")
    body = flask_client.get("/api/defects?with_fix_state=1",
                            headers=EDITOR).get_json()
    assert body["items"][0]["fix_state"] is None
