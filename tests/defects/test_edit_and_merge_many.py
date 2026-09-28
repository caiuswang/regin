"""Defect #194 — a defect's wording is editable after it is filed, and several
duplicates fold into one keeper in a single all-or-nothing merge that keeps
their evidence."""

from __future__ import annotations

import json

import pytest
from sqlmodel import select
from typer.testing import CliRunner

from cli.commands.defects import defects_app
from lib.auth import create_token
from lib.defects import images as defect_images
from lib.defects import store
from lib.orm import SessionLocal
from lib.orm.models.defects import SYMPTOM_MAX, Defect, DefectImage

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 16

runner = CliRunner()


def _row(defect_id):
    with SessionLocal() as session:
        return session.get(Defect, defect_id)


def _images(defect_id):
    with SessionLocal() as session:
        return [(i.idx, i.media_type) for i in session.exec(
            select(DefectImage).where(DefectImage.defect_id == defect_id)
            .order_by(DefectImage.idx)).all()]


# ── edit ──────────────────────────────────────────────────────


def test_edit_rewrites_the_wording_and_its_dedup_key():
    row = store.record("the gutter folds", session_id="t-1")
    before = row.activity_seq

    store.edit(row.id, symptom="  The gutter folds the NEWEST span ")

    after = _row(row.id)
    assert after.symptom == "The gutter folds the NEWEST span"
    assert after.symptom_key == "the gutter folds the newest span"
    assert after.activity_seq == before + 1
    # The new key is the dedup axis now: re-reporting it hits this row.
    again = store.record_reporting("the gutter folds the newest span",
                                   session_id="t-1")
    assert again.deduped and again.defect.id == row.id


def test_a_closed_defect_can_still_be_edited():
    row = store.record("typo'd report", session_id="t-1")
    store.close(row.id, missing_check="test_x")

    store.edit(row.id, symptom="the real report")

    after = _row(row.id)
    assert after.symptom == "the real report"
    assert after.status == "closed" and after.missing_check == "test_x"


@pytest.mark.parametrize("bad", ["", "   ", "x" * (SYMPTOM_MAX + 1)])
def test_edit_refuses_a_blank_or_oversized_symptom(bad):
    row = store.record("a", session_id="t-1")
    with pytest.raises(store.DefectError):
        store.edit(row.id, symptom=bad)
    assert _row(row.id).symptom == "a"


def test_edit_onto_another_rows_symptom_is_refused_and_names_merge(
        feature_row):
    feature_id = feature_row().id
    first = store.record("one", feature_id=feature_id)
    second = store.record("two", feature_id=feature_id)

    with pytest.raises(store.DefectError) as exc:
        store.edit(second.id, symptom="ONE")

    assert f"#{first.id}" in str(exc.value) and "merge" in str(exc.value)
    assert _row(second.id).symptom == "two"


def test_edit_onto_a_same_session_twin_is_refused():
    store.record("one", session_id="t-1")
    other = store.record("two", session_id="t-1")
    with pytest.raises(store.DefectError):
        store.edit(other.id, symptom="one")


def test_patch_edits_and_a_viewer_cannot(flask_client):
    row = store.record("old words", session_id="t-1")

    resp = flask_client.patch(f"/api/defects/{row.id}",
                              json={"symptom": "new words"}, headers=EDITOR)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["defect"]["symptom"] == "new words"

    assert flask_client.patch(f"/api/defects/{row.id}",
                              json={"symptom": "x"},
                              headers=VIEWER).status_code == 403
    for bad in ({}, {"symptom": ""}, {"symptom": 3},
                {"symptom": "x", "status": "closed"}):
        assert flask_client.patch(f"/api/defects/{row.id}", json=bad,
                                  headers=EDITOR).status_code == 400, bad
    assert flask_client.patch("/api/defects/9999", json={"symptom": "x"},
                              headers=EDITOR).status_code == 404


def test_cli_edit():
    row = store.record("old", session_id="t-1")
    result = runner.invoke(defects_app,
                           ["edit", str(row.id), "--symptom", "new"])
    assert result.exit_code == 0, result.output
    assert _row(row.id).symptom == "new"


# ── merge many ────────────────────────────────────────────────


def test_merge_many_folds_every_duplicate_and_keeps_their_evidence():
    keeper = store.record("keeper", session_id="t-1", suspect_paths=["k.py"])
    a = store.record("a", session_id="t-2", suspect_paths=["a.py", "k.py"])
    b = store.record("b", session_id="t-3", suspect_paths=["b.py"])
    defect_images.attach(keeper.id, PNG)
    defect_images.attach(a.id, GIF)
    defect_images.attach(a.id, PNG)
    defect_images.attach(b.id, GIF)

    out = store.merge_many([a.id, b.id], into=keeper.id)

    assert out.id == keeper.id and out.symptom == "keeper"
    assert _row(a.id) is None and _row(b.id) is None
    assert json.loads(_row(keeper.id).suspect_paths_json) == [
        "k.py", "a.py", "b.py"]
    assert _images(keeper.id) == [(1, "image/png"), (2, "image/gif"),
                                  (3, "image/png"), (4, "image/gif")]


def test_merge_many_adopts_the_one_fix_item_among_them(native_task):
    task = native_task(title="fix it")
    keeper = store.record("keeper", session_id="t-1")
    a = store.record("a", session_id="t-2")
    b = store.record("b", session_id="t-3")
    store.set_fix_item(b.id, task)

    store.merge_many([a.id, b.id], into=keeper.id)

    assert _row(keeper.id).fix_task_id == task


def _unchanged(*rows):
    for row in rows:
        now = _row(row.id)
        assert now is not None
        assert now.suspect_paths_json == row.suspect_paths_json


def test_merge_many_refuses_differing_fix_items_and_writes_nothing(
        native_task):
    keeper = store.record("keeper", session_id="t-1")
    a = store.record("a", session_id="t-2", suspect_paths=["a.py"])
    b = store.record("b", session_id="t-3")
    store.set_fix_item(a.id, native_task(title="one"))
    store.set_fix_item(b.id, native_task(title="two"))
    keeper, a, b = _row(keeper.id), _row(a.id), _row(b.id)

    with pytest.raises(store.DefectError):
        store.merge_many([a.id, b.id], into=keeper.id)

    _unchanged(keeper, a, b)
    assert _row(keeper.id).fix_task_id is None


def test_merge_many_refuses_hiding_an_open_bug_in_a_closed_keeper():
    keeper = store.record("keeper", session_id="t-1")
    store.close(keeper.id, missing_check="test_k")
    closed = store.record("closed dup", session_id="t-2")
    store.close(closed.id, missing_check="test_c")
    still_open = store.record("open dup", session_id="t-3")

    with pytest.raises(store.DefectError) as exc:
        store.merge_many([closed.id, still_open.id], into=keeper.id)

    assert f"#{still_open.id}" in str(exc.value) and "reopen" in str(exc.value)
    assert _row(closed.id) is not None and _row(still_open.id) is not None
    # Closed into closed is fine.
    store.merge_many([closed.id], into=keeper.id)
    assert _row(closed.id) is None


def test_merge_many_refuses_self_empty_and_missing():
    keeper = store.record("keeper", session_id="t-1")
    a = store.record("a", session_id="t-2")
    with pytest.raises(store.DefectError):
        store.merge_many([a.id, keeper.id], into=keeper.id)
    with pytest.raises(store.DefectError):
        store.merge_many([], into=keeper.id)
    with pytest.raises(store.DefectError):
        store.merge_many([a.id, 9999], into=keeper.id)
    assert _row(a.id) is not None


def test_absorb_route(flask_client):
    keeper = store.record("keeper", session_id="t-1")
    a = store.record("a", session_id="t-2", suspect_paths=["a.py"])
    b = store.record("b", session_id="t-3", suspect_paths=["b.py"])

    for bad in ({}, {"duplicates": []}, {"duplicates": [True]},
                {"duplicates": ["7"]}, {"duplicates": a.id}):
        assert flask_client.post(f"/api/defects/{keeper.id}/absorb",
                                 json=bad, headers=EDITOR).status_code == 400
    assert flask_client.post(f"/api/defects/{keeper.id}/absorb",
                             json={"duplicates": [a.id, 9999]},
                             headers=EDITOR).status_code == 404
    assert flask_client.post(f"/api/defects/{keeper.id}/absorb",
                             json={"duplicates": [a.id]},
                             headers=VIEWER).status_code == 403
    assert flask_client.post(f"/api/defects/{keeper.id}/absorb",
                             json={"duplicates": [keeper.id]},
                             headers=EDITOR).status_code == 409

    resp = flask_client.post(f"/api/defects/{keeper.id}/absorb",
                             json={"duplicates": [a.id, b.id]}, headers=EDITOR)
    assert resp.status_code == 200, resp.get_json()
    assert sorted(resp.get_json()["defect"]["suspect_paths"]) == [
        "a.py", "b.py"]
    assert _row(a.id) is None and _row(b.id) is None


def test_cli_merges_several_at_once():
    keeper = store.record("keeper", session_id="t-1")
    a = store.record("a", session_id="t-2")
    b = store.record("b", session_id="t-3")
    result = runner.invoke(defects_app, ["merge", str(a.id), str(b.id),
                                         "--into", str(keeper.id)])
    assert result.exit_code == 0, result.output
    assert f"#{a.id}, #{b.id} merged into #{keeper.id}" in result.output
    assert _row(a.id) is None and _row(b.id) is None
