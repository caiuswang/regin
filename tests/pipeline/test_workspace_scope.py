"""`?project=ws:<id>` scopes the Pipeline to a workspace's member repos (F167 S4)."""

from __future__ import annotations

import os

import pytest
from sqlmodel import text

from lib.orm import SessionLocal
from lib.orm.models import Repo, Workspace


def _file_feature(feature_id: int, project_id):
    with SessionLocal() as session:
        session.exec(text("UPDATE features SET project_id = :p WHERE id = :i")
                     .bindparams(p=project_id, i=feature_id))
        session.commit()


@pytest.fixture
def workspace(tmp_path, flask_client):
    """Workspace W rooted at <tmp>/ws with member `a`; repo `x` lives outside."""
    root = os.path.realpath(tmp_path / "ws")
    for name in ("a", "x-outside"):
        os.makedirs(os.path.join(root if name == "a" else str(tmp_path), name))
    with SessionLocal() as session:
        session.add(Repo(id=11, name="a", path=os.path.join(root, "a")))
        session.add(Repo(id=12, name="x", path=os.path.join(
            os.path.realpath(tmp_path), "x-outside")))
        ws = Workspace(name="W", root_path=root)
        session.add(ws)
        session.commit()
        session.refresh(ws)
        return ws.id


def _seed(feature_row, native_task, defect_row):
    member = feature_row("member feature")
    other = feature_row("non-member feature")
    loose = feature_row("no project feature")
    _file_feature(member.id, 11)
    _file_feature(other.id, 12)
    native_task(title="member task", project_id=11)
    native_task(title="non-member task", project_id=12)
    native_task(title="no project task")
    defect_row(member.id)
    defect_row(other.id)
    return member, other, loose


def _json(client, url):
    response = client.get(url)
    assert response.status_code == 200, (url, response.get_json())
    return response.get_json()


def test_workspace_scope_filters_the_pipeline_to_member_repos(
        flask_client, workspace, feature_row, native_task, defect_row):
    member, _, _ = _seed(feature_row, native_task, defect_row)
    scope = f"ws:{workspace}"
    _assert_overview_is_members_only(flask_client, scope, member.id)
    _assert_lists_are_members_only(flask_client, scope, member.id)


def _assert_overview_is_members_only(flask_client, scope, member_id):
    body = _json(flask_client, f"/api/pipeline/overview?project={scope}")
    assert [r["id"] for r in body["features_in_flight"]["rows"]] == [member_id]
    stages = {s["label"]: s["total"] for s in body["loop"]["stages"]}
    assert stages == {"Features": 1, "Tasks": 1, "Defects": 1}
    assert [r["id"] for r in body["projects"]["rows"]] == [11]


def _assert_lists_are_members_only(flask_client, scope, member_id):
    counts = _json(flask_client, f"/api/pipeline/counts?project={scope}")["counts"]
    assert counts == {"tasks": 1, "features": 1, "defects": 1}

    features = _json(flask_client, f"/api/features?project_id={scope}")
    assert [r["id"] for r in features["items"]] == [member_id]
    assert features["population"] == counts["features"]

    tasks = _json(flask_client, f"/api/tasks?project_id={scope}")
    assert [t["title"] for t in tasks["items"]] == ["member task"]
    assert tasks["population"] == counts["tasks"]

    defects = _json(flask_client, f"/api/defects?project={scope}")
    assert [d["feature_id"] for d in defects["items"]] == [member_id]

    projects = _json(flask_client, f"/api/pipeline/projects?project={scope}")
    assert [r["id"] for r in projects["items"]] == [11]


def test_workspace_scope_is_echoed_as_the_workspace(flask_client, workspace):
    scope = f"ws:{workspace}"
    body = _json(flask_client, f"/api/pipeline/overview?project={scope}")
    assert body["scope"] == scope
    tasks = _json(flask_client, f"/api/tasks?project_id={scope}")
    assert tasks["applied"]["filters"]["project_id"] == scope
    features = _json(flask_client, f"/api/features?project_id={scope}")
    assert features["applied"]["filters"]["project_id"] == scope
    panel = flask_client.get(f"/api/pipeline/projects/{scope}")
    assert panel.status_code == 400


def test_unknown_workspace_scope_falls_back_to_all(
        flask_client, workspace, feature_row, native_task, defect_row):
    _seed(feature_row, native_task, defect_row)
    everything = _json(flask_client, "/api/pipeline/counts")["counts"]
    stale = _json(flask_client, "/api/pipeline/counts?project=ws:9999")
    assert stale["counts"] == everything
    tasks = _json(flask_client, "/api/tasks?project_id=ws:9999")
    assert tasks["population"] == everything["tasks"]


def test_workspace_with_no_members_scopes_to_nothing(
        flask_client, tmp_path, feature_row, native_task, defect_row):
    _seed(feature_row, native_task, defect_row)
    with SessionLocal() as session:
        ws = Workspace(name="empty", root_path=os.path.realpath(tmp_path / "e"))
        session.add(ws)
        session.commit()
        session.refresh(ws)
        scope = f"ws:{ws.id}"
    counts = _json(flask_client, f"/api/pipeline/counts?project={scope}")
    assert counts["counts"] == {"tasks": 0, "features": 0, "defects": 0}
    features = _json(flask_client, f"/api/features?project_id={scope}")
    assert features["items"] == []


def test_repo_registered_later_joins_the_scope(
        flask_client, workspace, feature_row):
    late = feature_row("late member")
    with SessionLocal() as session:
        root = session.get(Workspace, workspace).root_path
        session.add(Repo(id=13, name="c", path=os.path.join(root, "c")))
        session.commit()
    _file_feature(late.id, 13)
    body = _json(flask_client, f"/api/features?project_id=ws:{workspace}")
    assert [r["id"] for r in body["items"]] == [late.id]
