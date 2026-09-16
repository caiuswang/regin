"""A feature's project — the column the Pipeline filtered on and nobody could set.

`features.project_id` shipped filterable (`lib/features/index.py` parses
`project_id=none` as a real scope) and with a picker above the Pipeline tabs,
but no create or edit path ever wrote it: `feature_store.create` took no
project, and `PATCH /api/features/<id>` read only `title` and `body`. Every
feature was `NULL`, so every per-project scope was empty and the picker filtered
a column only a hand-written UPDATE could fill.

A project is *where a feature lives*, not a step in its life, so setting one
appends no `feature_events` row — putting it in the append-only log would make
`rounds` count a filing.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from lib.auth import create_token
from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.features import FeatureEvent
from lib.orm.models.sync import Repo

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}


@pytest.fixture()
def repo(tmp_db) -> Repo:
    with SessionLocal() as session:
        row = Repo(name="regin", path="/tmp/regin")
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def _events(feature_id: int):
    with SessionLocal() as session:
        return session.exec(select(FeatureEvent).where(
            FeatureEvent.feature_id == feature_id)).all()


def test_the_ledger_carries_the_project_and_its_name(flask_client, repo):
    row = feature_store.create("a workbench")
    feature_store.set_project(row.id, repo.id)

    body = flask_client.get(f"/api/features/{row.id}", headers=EDITOR).get_json()

    assert body["project_id"] == repo.id
    assert body["project_name"] == "regin"


def test_an_unfiled_feature_carries_both_fields_as_null(flask_client):
    """Present-and-null, not absent: a picker that has to branch on a missing
    key renders "No project" and "we did not say" the same way."""
    row = feature_store.create("no project yet")

    body = flask_client.get(f"/api/features/{row.id}", headers=EDITOR).get_json()

    assert body["project_id"] is None
    assert body["project_name"] is None


def test_patching_the_project_files_the_feature(flask_client, repo):
    row = feature_store.create("a workbench")

    resp = flask_client.patch(f"/api/features/{row.id}",
                              json={"project_id": repo.id}, headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["project_id"] == repo.id
    assert feature_store.require(row.id).project_id == repo.id


def test_an_explicit_null_unfiles_the_feature(flask_client, repo):
    """"Move this to no project" is a request, not the absence of one."""
    row = feature_store.create("a workbench")
    feature_store.set_project(row.id, repo.id)

    resp = flask_client.patch(f"/api/features/{row.id}",
                              json={"project_id": None}, headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["project_id"] is None
    assert feature_store.require(row.id).project_id is None


def test_an_absent_key_leaves_the_project_alone(flask_client, repo):
    """The defect the presence check exists to prevent: a title edit that
    silently unfiles the feature it renamed."""
    row = feature_store.create("a workbench")
    feature_store.set_project(row.id, repo.id)

    resp = flask_client.patch(f"/api/features/{row.id}",
                              json={"title": "renamed"}, headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["project_id"] == repo.id


def test_an_unregistered_project_is_a_400_naming_the_field(flask_client):
    row = feature_store.create("a workbench")

    resp = flask_client.patch(f"/api/features/{row.id}",
                              json={"project_id": 9999}, headers=EDITOR)

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["field"] == "project_id"
    assert "9999" in body["error"]
    assert feature_store.require(row.id).project_id is None


@pytest.mark.parametrize("value", ["26", True, 1.5, []])
def test_a_non_integer_project_is_a_400_naming_the_field(flask_client, value):
    """`True` included deliberately: `isinstance(True, int)` is true in Python,
    so a bare int check would file the feature under repo #1."""
    row = feature_store.create("a workbench")

    resp = flask_client.patch(f"/api/features/{row.id}",
                              json={"project_id": value}, headers=EDITOR)

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "project_id"


def test_a_viewer_cannot_change_the_project(flask_client, repo):
    row = feature_store.create("a workbench")

    resp = flask_client.patch(f"/api/features/{row.id}",
                              json={"project_id": repo.id}, headers=VIEWER)

    assert resp.status_code == 403
    assert feature_store.require(row.id).project_id is None


def test_filing_a_feature_appends_no_event(flask_client, repo):
    row = feature_store.create("a workbench")
    before = len(_events(row.id))

    flask_client.patch(f"/api/features/{row.id}",
                       json={"project_id": repo.id}, headers=EDITOR)

    assert len(_events(row.id)) == before


def test_the_index_scopes_to_the_project_once_it_is_set(flask_client, repo):
    """The whole point: a scope the picker offers has to return rows."""
    filed = feature_store.create("filed")
    feature_store.set_project(filed.id, repo.id)
    feature_store.create("unfiled")

    scoped = flask_client.get(f"/api/features?project_id={repo.id}",
                              headers=EDITOR).get_json()
    unfiled = flask_client.get("/api/features?project_id=none",
                               headers=EDITOR).get_json()

    assert [r["title"] for r in scoped["items"]] == ["filed"]
    assert [r["title"] for r in unfiled["items"]] == ["unfiled"]


def test_every_ledger_route_answers_with_the_same_body(flask_client, repo):
    """A PATCH that re-seats the page must not hand back a thinner ledger than
    the GET did: `stage_live` came back absent, the picker's parent evaluated
    `!undefined` as true, and a refresh-from-remote control appeared on a
    native feature until the next reload."""
    row = feature_store.create("a workbench")

    got = flask_client.get(f"/api/features/{row.id}", headers=EDITOR).get_json()
    patched = flask_client.patch(f"/api/features/{row.id}",
                                 json={"project_id": repo.id},
                                 headers=EDITOR).get_json()
    shipped = flask_client.post(f"/api/features/{row.id}/ship",
                                headers=EDITOR).get_json()

    assert set(patched) == set(got) == set(shipped)
    assert patched["stage_live"] is True
    assert "synced_at" in patched
