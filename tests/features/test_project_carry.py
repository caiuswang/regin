"""Project auto-carry (feature #99): a row created from a known endpoint gets
that endpoint's project unless the request states one.

The rule keys on whether `project_id` is PRESENT: an explicit `null` is an
operator's "No project" and is honoured, and only an absent key is carried.
"""

from __future__ import annotations

import pytest

from lib.auth import create_token
from lib.features import store as feature_store
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import AgentTask
from lib.tasks import store as task_store
from lib.tasks.transcribe import transcribe

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}


def _repo(name: str) -> Repo:
    with SessionLocal() as session:
        row = Repo(name=name, path=f"/tmp/{name}")
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


@pytest.fixture()
def r1(tmp_db) -> Repo:
    return _repo("r1")


@pytest.fixture()
def r2(tmp_db) -> Repo:
    return _repo("r2")


def _filed_feature(project_id):
    row = feature_store.create("a feature")
    if project_id is not None:
        feature_store.set_project(row.id, project_id)
    return row


def _post_task(client, **body):
    resp = client.post("/api/tasks", json={"title": "step", **body},
                       headers=EDITOR)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["task"]


def test_task_created_under_a_feature_inherits_its_project(flask_client, r1):
    feature = _filed_feature(r1.id)
    assert _post_task(flask_client, feature_id=feature.id)["project_id"] == r1.id


def test_explicit_null_is_honoured(flask_client, r1):
    feature = _filed_feature(r1.id)
    task = _post_task(flask_client, feature_id=feature.id, project_id=None)
    assert task["project_id"] is None


def test_explicit_other_project_is_honoured(flask_client, r1, r2):
    feature = _filed_feature(r1.id)
    task = _post_task(flask_client, feature_id=feature.id, project_id=r2.id)
    assert task["project_id"] == r2.id


def test_unregistered_project_is_still_refused(flask_client, r1):
    feature = _filed_feature(r1.id)
    resp = flask_client.post(
        "/api/tasks", json={"title": "s", "feature_id": feature.id,
                            "project_id": 99999}, headers=EDITOR)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "project_id"


def test_projectless_feature_carries_nothing(flask_client, tmp_db):
    feature = _filed_feature(None)
    assert _post_task(flask_client, feature_id=feature.id)["project_id"] is None


def test_no_feature_and_no_project_is_unfiled(flask_client, r1):
    assert _post_task(flask_client)["project_id"] is None


def test_carry_is_a_copy_not_a_link(flask_client, r1, r2):
    """Invariant 2: a feature's project changing later retargets nothing."""
    feature = _filed_feature(r1.id)
    task = _post_task(flask_client, feature_id=feature.id)
    feature_store.set_project(feature.id, r2.id)
    assert task_store.get_task(task["id"])["project_id"] == r1.id


def test_fix_task_from_a_defect_carries_the_features_project(
        flask_client, r1):
    from lib.defects import store as defect_store
    feature = _filed_feature(r1.id)
    defect = defect_store.record("it broke", feature_id=feature.id)
    resp = flask_client.post(f"/api/defects/{defect.id}/fix-item",
                             json={"create": True}, headers=EDITOR)
    assert resp.status_code in (200, 201), resp.get_json()
    fix_task_id = defect_store.get(defect.id).fix_task_id
    with SessionLocal() as session:
        assert session.get(AgentTask, fix_task_id).project_id == r1.id


def test_transcribed_slices_carry_the_features_project(r1):
    feature = _filed_feature(r1.id)
    transcribe(feature.id, (
        "## Work slices\n\n"
        "| # | Slice | Delivers | Spec refs | Tests | Depends on | Base | "
        "Schema |\n|---|---|---|---|---|---|---|---|\n"
        "| S0 | contracts | the contracts | R1 | test_a | — | `master` | |\n"),
        branch_prefix="f99/")
    with SessionLocal() as session:
        from sqlmodel import select
        rows = session.exec(select(AgentTask).where(
            AgentTask.feature_id == feature.id)).all()
    assert [row.project_id for row in rows] == [r1.id]


def test_feature_created_with_a_project(flask_client, r1):
    resp = flask_client.post("/api/features",
                             json={"title": "x", "project_id": r1.id},
                             headers=EDITOR)
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["project_id"] == r1.id
    assert feature_store.require(resp.get_json()["id"]).project_id == r1.id


def test_feature_created_with_an_unknown_project_is_refused(flask_client, r1):
    resp = flask_client.post("/api/features",
                             json={"title": "y", "project_id": 99999},
                             headers=EDITOR)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "project_id"


def test_feature_created_with_a_non_integer_project_is_refused(flask_client,
                                                               r1):
    resp = flask_client.post("/api/features",
                             json={"title": "y", "project_id": "1"},
                             headers=EDITOR)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "project_id"


def test_adopted_feature_is_filed_under_the_project(flask_client, r1,
                                                    tracker_issue):
    tracker_issue("CAI-9", title="adopted")
    resp = flask_client.post("/api/features",
                             json={"ref": "CAI-9", "project_id": r1.id},
                             headers=EDITOR)
    assert resp.status_code == 201, resp.get_json()
    assert feature_store.require(resp.get_json()["id"]).project_id == r1.id


def test_adopt_with_an_unknown_project_writes_nothing(flask_client, r1,
                                                      tracker_issue):
    tracker_issue("CAI-10")
    resp = flask_client.post("/api/features",
                             json={"ref": "CAI-10", "project_id": 99999},
                             headers=EDITOR)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "project_id"
    assert feature_store.by_external("linear", "CAI-10") is None


def test_task_detail_feature_block_carries_the_project(flask_client, r1):
    feature = _filed_feature(r1.id)
    task = _post_task(flask_client, feature_id=feature.id)
    body = flask_client.get(f"/api/tasks/{task['id']}", headers=EDITOR).get_json()
    assert body["feature"]["project_id"] == r1.id
    assert body["feature"]["project_name"] == "r1"


# F177 — follow-ups and the first filing of a feature.

def _tasks_of(feature_id: int) -> list[AgentTask]:
    return feature_store.tasks_of(feature_id)


def _patch_project(client, feature_id, project_id):
    resp = client.patch(f"/api/features/{feature_id}",
                        json={"project_id": project_id}, headers=EDITOR)
    assert resp.status_code == 200, resp.get_json()


def test_follow_up_created_with_the_followed_features_project(flask_client,
                                                              r1):
    from lib.features import links
    original = _filed_feature(r1.id)
    resp = flask_client.post("/api/features",
                             json={"title": "next", "follows": original.id,
                                   "project_id": r1.id}, headers=EDITOR)
    assert resp.status_code == 201, resp.get_json()
    new_id = resp.get_json()["id"]
    assert feature_store.require(new_id).project_id == r1.id
    follows = [ref["id"] for ref in links.for_feature(new_id)["follows"]]
    assert follows == [original.id]


def test_follow_up_states_its_own_project_over_the_followed(flask_client,
                                                            r1, r2):
    original = _filed_feature(r1.id)
    resp = flask_client.post("/api/features",
                             json={"title": "next", "follows": original.id,
                                   "project_id": r2.id}, headers=EDITOR)
    assert resp.status_code == 201, resp.get_json()
    assert feature_store.require(resp.get_json()["id"]).project_id == r2.id


def test_first_filing_carries_the_project_onto_unfiled_tasks(flask_client,
                                                              r1):
    feature = _filed_feature(None)
    _post_task(flask_client, feature_id=feature.id)
    _post_task(flask_client, feature_id=feature.id)
    _patch_project(flask_client, feature.id, r1.id)
    assert feature_store.require(feature.id).project_id == r1.id
    assert [t.project_id for t in _tasks_of(feature.id)] == [r1.id, r1.id]


def test_first_filing_leaves_an_explicitly_filed_task(flask_client, r1, r2):
    feature = _filed_feature(None)
    _post_task(flask_client, feature_id=feature.id)
    _post_task(flask_client, feature_id=feature.id, project_id=r2.id)
    _patch_project(flask_client, feature.id, r1.id)
    assert [t.project_id for t in _tasks_of(feature.id)] == [r1.id, r2.id]


def test_refiling_a_feature_carries_nothing(flask_client, r1, r2):
    feature = _filed_feature(r1.id)
    _post_task(flask_client, feature_id=feature.id, project_id=None)
    _patch_project(flask_client, feature.id, r2.id)
    assert [t.project_id for t in _tasks_of(feature.id)] == [None]


def test_unfiling_a_feature_keeps_its_tasks_filed(flask_client, r1):
    feature = _filed_feature(None)
    _post_task(flask_client, feature_id=feature.id)
    _patch_project(flask_client, feature.id, r1.id)
    _patch_project(flask_client, feature.id, None)
    assert feature_store.require(feature.id).project_id is None
    assert [t.project_id for t in _tasks_of(feature.id)] == [r1.id]
