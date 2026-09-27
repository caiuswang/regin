"""The Projects tab: one rollup per repo, and one project's panel."""

from __future__ import annotations

import pytest
from sqlmodel import text

from lib.auth import create_token
from lib.features import events
from lib.orm import SessionLocal
from lib.pipeline import counts as counts_module
from lib.pipeline import projects

VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}


def _repo(name: str, *, active: int = 1) -> int:
    with SessionLocal() as session:
        session.exec(text(
            "INSERT INTO repos (name, path, description, is_active)"
            " VALUES (:n, :p, :d, :a)"
        ).bindparams(n=name, p=f"/src/{name}", d=f"{name} repo", a=active))
        session.commit()
        return session.exec(text(
            "SELECT id FROM repos WHERE name = :n").bindparams(n=name)).one()[0]


def _file(feature_id: int, project_id):
    with SessionLocal() as session:
        session.exec(text(
            "UPDATE features SET project_id = :p WHERE id = :i"
        ).bindparams(p=project_id, i=feature_id))
        session.commit()


def _all():
    return projects.listing(projects.parse_filters({}), page=0,
                            size=200)["items"]


def _by_scope(items):
    return {item["scope"]: item for item in items}


@pytest.mark.parametrize("stages, expected", [
    ({}, projects.STATUS_EMPTY),
    ({"abandoned": 2}, projects.STATUS_EMPTY),
    ({"building": 1, "shipped": 3}, projects.STATUS_ACTIVE),
    ({"spec": 1, "shipped": 3}, projects.STATUS_PLANNED),
    ({"shipped": 2, "escaped": 1, "abandoned": 1}, projects.STATUS_COMPLETED),
])
def test_status_decision_table(stages, expected):
    assert projects.status_of(stages) == expected


def _seed_alpha(feature_row, defect_row, native_task) -> int:
    rid = _repo("alpha")
    shipped = feature_row("shipped one", stage=events.SHIPPED)
    building = feature_row("building one", stage=events.BUILD_STARTED)
    dropped = feature_row("dropped", stage=events.ABANDONED)
    loose = feature_row("loose")
    for f in (shipped, building, dropped):
        _file(f.id, rid)
    defect_row(building.id)
    defect_row(building.id, status="closed")
    defect_row(loose.id)
    defect_row(None)
    native_task(title="a", project_id=rid)
    native_task(title="b", project_id=rid, status="done")
    native_task(title="c")
    return rid


def test_list_rolls_up_a_project(flask_client, feature_row, defect_row,
                                 native_task):
    rid = _seed_alpha(feature_row, defect_row, native_task)

    body = flask_client.get("/api/pipeline/projects", headers=VIEWER).get_json()
    alpha = _by_scope(body["items"])[str(rid)]

    assert alpha["name"] == "alpha"
    assert alpha["registered"] is True
    assert alpha["status"] == projects.STATUS_ACTIVE
    assert alpha["progress"] == {"done": 1, "live": 2}
    assert alpha["features"]["by_stage"] == {
        "shipped": 1, "building": 1, "abandoned": 1}
    assert alpha["tasks"] == {"total": 2, "by_status": {"idea": 1, "done": 1}}
    assert alpha["defects"] == {"total": 2, "open": 1}


def test_list_ends_with_the_no_project_scope(flask_client, feature_row,
                                             defect_row, native_task):
    _seed_alpha(feature_row, defect_row, native_task)

    items = flask_client.get("/api/pipeline/projects",
                             headers=VIEWER).get_json()["items"]
    none = items[-1]

    assert none["scope"] == "none"
    assert none["features"]["total"] == 1
    assert none["defects"] == {"total": 2, "open": 2}
    assert none["tasks"]["total"] == 1


def test_every_row_agrees_with_the_tab_counts(flask_client, feature_row,
                                              defect_row, native_task):
    rid = _repo("beta")
    filed = feature_row("filed")
    _file(filed.id, rid)
    _file(feature_row("orphaned").id, 999)
    defect_row(filed.id)
    defect_row(None)
    native_task(title="t", project_id=rid, status="archived")
    native_task(title="u", project_id=999)

    items = _all()
    for item in items:
        counted = counts_module.counts(counts_module.parse_scope(item["scope"]))
        assert item["tasks"]["total"] == counted["tasks"], item["scope"]
        assert item["features"]["total"] == counted["features"], item["scope"]
        assert item["defects"]["total"] == counted["defects"], item["scope"]

    everything = counts_module.counts("all")
    assert sum(i["features"]["total"] for i in items) == everything["features"]
    assert sum(i["tasks"]["total"] for i in items) == everything["tasks"]
    assert sum(i["defects"]["total"] for i in items) == everything["defects"]

    orphan = _by_scope(items)["999"]
    assert orphan["registered"] is False and orphan["name"] is None


def test_inactive_repo_without_rows_is_not_listed(flask_client):
    _repo("gone", active=0)
    names = [i["name"] for i in _all()]
    assert "gone" not in names


def test_projects_with_work_sort_first(flask_client, feature_row):
    _repo("aaa-empty")
    busy = _repo("zzz-busy")
    _file(feature_row("x").id, busy)

    names = [i["name"] for i in _all()]
    assert names.index("zzz-busy") < names.index("aaa-empty")


def test_panel_lists_features_defects_and_tasks(flask_client, feature_row,
                                                defect_row, native_task):
    rid = _repo("gamma")
    f = feature_row("gamma feature", stage=events.BUILD_STARTED)
    _file(f.id, rid)
    feature_row("elsewhere")
    defect_row(f.id, symptom="it broke")
    defect_row(f.id, status="closed")
    native_task(title="live task", project_id=rid)
    native_task(title="archived task", project_id=rid, status="archived")

    body = flask_client.get(f"/api/pipeline/projects/{rid}",
                            headers=VIEWER).get_json()

    assert body["project"]["name"] == "gamma"
    assert [i["title"] for i in body["features"]["items"]] == ["gamma feature"]
    assert body["features"]["matched"] == body["project"]["features"]["total"]
    assert body["features"]["truncated"] is False
    assert [d["symptom"] for d in body["open_defects"]] == ["it broke"]
    assert body["open_defects"][0]["feature_title"] == "gamma feature"
    assert [t["title"] for t in body["recent_tasks"]] == ["live task"]


def test_panel_feature_list_matches_the_features_tab(flask_client, feature_row):
    rid = _repo("delta")
    for n in range(3):
        _file(feature_row(f"f{n}").id, rid)

    panel = flask_client.get(f"/api/pipeline/projects/{rid}",
                             headers=VIEWER).get_json()
    tab = flask_client.get(f"/api/features?project_id={rid}",
                           headers=VIEWER).get_json()
    assert panel["features"]["matched"] == tab["pagination"]["total"] == 3


def test_no_project_panel_holds_unattributed_defects(flask_client, defect_row):
    defect_row(None, symptom="nobody's")
    body = flask_client.get("/api/pipeline/projects/none",
                            headers=VIEWER).get_json()
    assert body["project"]["scope"] == "none"
    assert [d["symptom"] for d in body["open_defects"]] == ["nobody's"]
    assert body["open_defects"][0]["feature_id"] is None


def test_empty_registered_project_has_a_panel(flask_client):
    rid = _repo("empty")
    body = flask_client.get(f"/api/pipeline/projects/{rid}",
                            headers=VIEWER).get_json()
    assert body["project"]["status"] == projects.STATUS_EMPTY
    assert body["project"]["progress"] == {"done": 0, "live": 0}
    assert body["features"]["items"] == []


@pytest.mark.parametrize("scope, code", [
    ("424242", 404), ("all", 400), ("nonsense", 400),
])
def test_panel_refuses_what_is_not_one_project(flask_client, scope, code):
    resp = flask_client.get(f"/api/pipeline/projects/{scope}", headers=VIEWER)
    assert resp.status_code == code
    assert "error" in resp.get_json()


def test_panel_tile_and_open_work_count_the_same_tasks(flask_client,
                                                       feature_row,
                                                       native_task):
    rid = _repo("delta")
    f = feature_row("delta feature", stage=events.BUILD_STARTED)
    _file(f.id, rid)
    native_task(title="a step", project_id=rid, feature_id=f.id)
    native_task(title="top level", project_id=rid)

    body = flask_client.get(f"/api/pipeline/projects/{rid}",
                            headers=VIEWER).get_json()

    assert body["project"]["tasks"]["total"] == len(body["recent_tasks"]) == 2
    assert sorted(t["title"] for t in body["recent_tasks"]) == [
        "a step", "top level"]
