"""Feature #119 S6 — defect verbs on the web: the `triage=` facet, unsnooze and
restore, suspect-path edits, and refusals that carry their remedy."""

from __future__ import annotations

from lib.auth import create_token
from lib.defects import store
from lib.features import events

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}


def _ids(client, query):
    body = client.get(f"/api/defects?{query}", headers=EDITOR).get_json()
    return {item["id"] for item in body["items"]}, body


def _triage_population(feature_row):
    feature = feature_row("the feature")
    queued = store.record("still waiting", session_id="t-1")
    snoozed = store.snooze(store.record("held out", session_id="t-2").id)
    woken = store.snooze(store.record("woken", session_id="t-3").id)
    store.edit(woken.id, suspect_paths=["a.py"])
    declined = store.decline(store.record("not a bug", session_id="t-4").id)
    store.record("blamed", feature_id=feature.id)
    return queued, snoozed, woken, declined


def test_defects_api_triage_filter(flask_client, feature_row):
    queued, snoozed, woken, declined = _triage_population(feature_row)
    ids, body = _ids(flask_client, "triage=queue")
    assert ids == {queued.id, woken.id}
    assert body["applied"]["filters"]["triage"] == "queue"
    assert body["pagination"]["total"] == 2
    assert ids == _ids(flask_client, "scope=triage")[0]

    assert _ids(flask_client, "triage=snoozed")[0] == {snoozed.id}
    assert _ids(flask_client, "triage=declined")[0] == {declined.id}
    assert _ids(flask_client, "triage=declined&status=open")[0] == set()

    # The queue has no project; a project scope is dropped, not applied.
    assert _ids(flask_client, "triage=queue&project=4")[0] == ids



def test_defects_api_refuses_unknown_triage(flask_client):
    bad = flask_client.get("/api/defects?triage=later", headers=EDITOR)
    assert bad.status_code == 400
    assert "unknown triage" in bad.get_json()["error"]


def test_defect_rows_carry_queue_position(flask_client, feature_row):
    queued, snoozed, woken, _ = _triage_population(feature_row)
    rows = {item["id"]: item for item in
            _ids(flask_client, "status=open&size=50")[1]["items"]}
    assert rows[queued.id]["queue_position"] == "queue"
    assert rows[snoozed.id]["queue_position"] == "snoozed"
    assert rows[woken.id]["queue_position"] == "queue"


def test_defect_unsnooze_and_restore_keep_round(flask_client):
    snoozed = store.snooze(store.record("held", session_id="t-1").id)
    res = flask_client.post(f"/api/defects/{snoozed.id}/unsnooze",
                            headers=EDITOR)
    assert res.status_code == 200
    row = res.get_json()["defect"]
    assert row["triage_state"] is None
    assert row["queue_position"] == "queue"
    assert row["round"] == snoozed.round

    again = flask_client.post(f"/api/defects/{snoozed.id}/unsnooze",
                              headers=EDITOR)
    assert again.status_code == 409
    _restore_keeps_round(flask_client)


def _restore_keeps_round(flask_client):
    declined = store.decline(store.record("nope", session_id="t-2").id,
                             reason="works as intended")
    res = flask_client.post(f"/api/defects/{declined.id}/restore",
                            headers=EDITOR)
    assert res.status_code == 200
    row = res.get_json()["defect"]
    assert (row["status"], row["triage_state"], row["triage_reason"],
            row["missing_check"], row["closed_at"]) == \
        ("open", None, None, None, None)
    assert row["round"] == declined.round
    assert row["queue_position"] == "queue"
    _restore_refuses_a_fixed_defect(flask_client)


def _restore_refuses_a_fixed_defect(flask_client):
    fixed = store.close(store.record("fixed", session_id="t-3").id,
                        missing_check="test_x")
    refused = flask_client.post(f"/api/defects/{fixed.id}/restore",
                                headers=EDITOR)
    assert refused.status_code == 409
    assert refused.get_json()["remedy"] == {
        "verb": "reopen", "args": {"defect_id": fixed.id}}
    assert store.get(fixed.id).round == fixed.round

    missing = flask_client.post("/api/defects/99999/restore", headers=EDITOR)
    assert missing.status_code == 404


def test_defect_edit_suspect_paths(flask_client):
    row = store.record("the gutter folds", session_id="t-1",
                       suspect_paths=["a.py"])
    res = flask_client.patch(
        f"/api/defects/{row.id}", headers=EDITOR,
        json={"suspect_paths": [" b.vue ", "", "c.py", "b.vue"]})
    assert res.status_code == 200
    body = res.get_json()["defect"]
    assert body["suspect_paths"] == ["b.vue", "c.py"]
    assert body["symptom"] == "the gutter folds"

    both = flask_client.patch(
        f"/api/defects/{row.id}", headers=EDITOR,
        json={"symptom": "the gutter folds twice", "suspect_paths": []})
    assert both.status_code == 200
    assert both.get_json()["defect"]["suspect_paths"] == []
    assert both.get_json()["defect"]["symptom"] == "the gutter folds twice"

    for bad in ({}, {"suspect_paths": "a.py"}, {"suspect_paths": [1]},
                {"symptom": " "}, {"paths": []}):
        res = flask_client.patch(f"/api/defects/{row.id}", headers=EDITOR,
                                 json=bad)
        assert res.status_code == 400, bad


def test_defect_refusal_carries_remedy(flask_client, feature_row):
    feature = feature_row("the feature")
    keeper = store.record("same symptom", feature_id=feature.id)
    stray = store.record("same symptom", session_id="t-1")

    res = flask_client.post(f"/api/defects/{stray.id}/attribute",
                            headers=EDITOR, json={"feature_id": feature.id})
    assert res.status_code == 409
    assert res.get_json()["remedy"] == {
        "verb": "merge", "args": {"defect_id": stray.id, "into": keeper.id}}

    store.close(keeper.id, missing_check="test_same")
    res = flask_client.post("/api/defects", headers=EDITOR, json={
        "symptom": "same symptom", "feature_id": feature.id})
    assert res.status_code == 409
    assert res.get_json()["remedy"] == {
        "verb": "reopen", "args": {"defect_id": keeper.id}}

    _remedy_attribute_and_reopen(flask_client, feature, keeper, stray)


def _remedy_attribute_and_reopen(flask_client, feature, keeper, stray):
    res = flask_client.post(f"/api/defects/{stray.id}/fix-item",
                            headers=EDITOR, json={"create": True})
    assert res.status_code == 409
    assert res.get_json()["remedy"] == {
        "verb": "attribute", "args": {"defect_id": stray.id}}

    other = store.record("another", feature_id=feature.id)
    res = flask_client.post(f"/api/defects/{other.id}/merge",
                            headers=EDITOR, json={"into": keeper.id})
    assert res.status_code == 409
    assert res.get_json()["remedy"] == {
        "verb": "reopen", "args": {"defect_id": keeper.id}}

    # A refusal that names no other verb carries none.
    res = flask_client.post(f"/api/defects/{other.id}/reopen", headers=EDITOR)
    assert res.status_code == 409
    assert "remedy" not in res.get_json()


def test_defect_fix_item_clear(flask_client, feature_row, native_task):
    feature = feature_row("the feature")
    row = store.record("needs a fix", feature_id=feature.id)
    store.set_fix_item(row.id, native_task(title="fix it"))
    res = flask_client.delete(f"/api/defects/{row.id}/fix-item",
                              headers=EDITOR)
    assert res.status_code == 200
    assert res.get_json()["defect"]["fix_task_id"] is None


def test_feature_picker_server_search_excludes_abandoned(flask_client,
                                                         feature_row):
    live = feature_row("ledger rollups")
    gone = feature_row("ledger retired")
    events.append(gone.id, events.STAGE_ABANDONED, declared_by=events.OPERATOR)
    feature_row("unrelated")

    body = flask_client.get("/api/features?q=ledger&size=20",
                            headers=EDITOR).get_json()
    assert [item["id"] for item in body["items"]] == [live.id]
    assert body["pagination"]["total"] == 1
