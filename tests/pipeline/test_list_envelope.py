"""Feature #108 — one list envelope across the four Pipeline tabs.

Every list endpoint answers `ListPage`: items, offset pagination, the
unnarrowed `population` under the shell's project scope, and `applied` — what
the server actually used. The toolbar count (population) and the tab strip
(`/api/pipeline/counts`) are the same number under the same scope (I1).
"""

from __future__ import annotations

import pytest
from sqlmodel import text

from lib.auth import create_token
from lib.orm import SessionLocal

VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}

LISTS = ["/api/tasks", "/api/features", "/api/defects",
         "/api/pipeline/projects"]


def _repo(name: str) -> int:
    with SessionLocal() as session:
        session.exec(text(
            "INSERT INTO repos (name, path, description, is_active)"
            " VALUES (:n, :p, :d, 1)"
        ).bindparams(n=name, p=f"/src/{name}", d=f"{name} repo"))
        session.commit()
        return session.exec(text(
            "SELECT id FROM repos WHERE name = :n").bindparams(n=name)).one()[0]


def _file(feature_id: int, project_id):
    with SessionLocal() as session:
        session.exec(text(
            "UPDATE features SET project_id = :p WHERE id = :i"
        ).bindparams(p=project_id, i=feature_id))
        session.commit()


@pytest.mark.parametrize("path", LISTS)
def test_every_list_answers_the_envelope(flask_client, path):
    body = flask_client.get(path, headers=VIEWER).get_json()

    assert isinstance(body["items"], list)
    assert {"page", "size", "total", "has_next", "has_prev"} <= set(
        body["pagination"])
    assert isinstance(body["population"], int)
    assert ":" in body["applied"]["sort"]
    assert isinstance(body["applied"]["filters"], dict)


@pytest.fixture
def seeded(feature_row, defect_row, native_task):
    rid = _repo("alpha")
    mine = feature_row("mine")
    _file(mine.id, rid)
    feature_row("loose")
    defect_row(mine.id)
    defect_row(None)
    native_task(title="in alpha", project_id=rid)
    native_task(title="archived", project_id=rid, status="archived")
    native_task(title="a step", project_id=rid, feature_id=mine.id)
    native_task(title="backlog")
    return rid


@pytest.mark.parametrize("scope", ["all", "none", "repo"])
def test_population_equals_the_tab_count(flask_client, seeded, scope):
    project = str(seeded) if scope == "repo" else scope
    counts = flask_client.get(f"/api/pipeline/counts?project={project}",
                              headers=VIEWER).get_json()["counts"]
    param = "" if project == "all" else project
    reads = {
        "tasks": f"/api/tasks?root_only=1&project_id={param}",
        "features": f"/api/features?project_id={param}",
        "defects": f"/api/defects?project={param}",
    }
    for key, url in reads.items():
        body = flask_client.get(url, headers=VIEWER).get_json()
        assert body["population"] == counts[key], (key, scope)
        # No narrowing: what the list pages through is the population too.
        assert body["pagination"]["total"] == counts[key], (key, scope)


def test_unknown_sort_falls_back_and_is_echoed(flask_client):
    for path, default in [("/api/tasks", "created:desc"),
                          ("/api/features", "created:desc"),
                          ("/api/defects", "opened:desc"),
                          ("/api/pipeline/projects", "activity:desc")]:
        body = flask_client.get(f"{path}?sort=bogus", headers=VIEWER).get_json()
        assert body["applied"]["sort"] == default, path


def test_projects_narrow_server_side(flask_client, feature_row):
    _repo("regin-core")
    _repo("other")

    body = flask_client.get("/api/pipeline/projects?q=reg",
                            headers=VIEWER).get_json()

    assert [i["name"] for i in body["items"]] == ["regin-core"]
    assert body["pagination"]["total"] == 1
    assert body["population"] == 3  # two repos + "No project"
    assert body["applied"]["filters"] == {"q": "reg"}


def test_projects_page_by_offset_and_sort(flask_client):
    for name in ("b", "a", "c"):
        _repo(name)

    first = flask_client.get("/api/pipeline/projects?sort=name:asc&size=2",
                             headers=VIEWER).get_json()
    second = flask_client.get(
        "/api/pipeline/projects?sort=name:asc&size=2&page=1",
        headers=VIEWER).get_json()

    assert [i["name"] for i in first["items"]] == ["a", "b"]
    assert first["pagination"]["has_next"] is True
    # "No project" stays last under every sort.
    assert [i["scope"] for i in second["items"]][-1] == "none"
    assert second["items"][0]["name"] == "c"


def test_projects_refuse_an_unknown_status(flask_client):
    resp = flask_client.get("/api/pipeline/projects?status=later",
                            headers=VIEWER)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "status"
