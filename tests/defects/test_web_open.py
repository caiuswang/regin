"""`POST /api/defects` — opening a defect from the browser (regin:79).

The route is the CLI's `defects open` behind the JWT gate, and it keeps the
store's `opened` / `deduped` distinction on the wire: the caller is often an
agent, and a silent no-op read as a successful open is how a defect goes
unreported. `session_id` is never taken from the body — a defect opened on a
page belongs to no trace.
"""

from __future__ import annotations

from sqlmodel import select

from lib.auth import create_token
from lib.orm import SessionLocal
from lib.orm.models.defects import Defect

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}


def _rows():
    with SessionLocal() as session:
        return session.exec(select(Defect)).all()


def test_post_opens_a_defect_against_the_feature(flask_client,
                                                feature_row):
    feature = feature_row()

    resp = flask_client.post("/api/defects", json={
        "symptom": "the gutter folds the newest span",
        "feature_id": feature.id, "suspect_paths": ["a.py"],
    }, headers=EDITOR)

    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["outcome"] == "opened"
    rows = _rows()
    assert len(rows) == 1
    assert body["defect"]["id"] == rows[0].id
    assert rows[0].feature_id == feature.id
    assert rows[0].reported_by_trace is None
    assert rows[0].suspect_paths_json == '["a.py"]'


def test_a_second_post_of_the_same_symptom_is_deduped_not_a_second_row(
        flask_client, feature_row):
    feature = feature_row()
    payload = {"symptom": "It Broke", "feature_id": feature.id}
    first = flask_client.post("/api/defects", json=payload, headers=EDITOR)

    again = flask_client.post("/api/defects", json={
        "symptom": "  it   broke ", "feature_id": feature.id},
        headers=EDITOR)

    assert again.status_code == 200, again.get_json()
    assert again.get_json()["outcome"] == "deduped"
    assert again.get_json()["defect"]["id"] == first.get_json()["defect"]["id"]
    assert len(_rows()) == 1


def test_the_body_cannot_name_a_session(flask_client, feature_row):
    feature = feature_row()

    resp = flask_client.post("/api/defects", json={
        "symptom": "x", "feature_id": feature.id,
        "session_id": "t-forged"}, headers=EDITOR)

    assert resp.status_code == 400
    assert not _rows()


def test_a_symptom_is_required(flask_client):
    resp = flask_client.post("/api/defects", json={"symptom": "  "},
                             headers=EDITOR)
    assert resp.status_code == 400


def test_anonymous_and_viewers_cannot_open(anon_client, flask_client):
    assert anon_client.post("/api/defects",
                            json={"symptom": "x"}).status_code == 401
    assert flask_client.post("/api/defects", json={"symptom": "x"},
                             headers=VIEWER).status_code == 403
    assert not _rows()
