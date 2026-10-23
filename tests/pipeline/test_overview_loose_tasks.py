"""Open tasks under no feature reach the Overview (feature #171)."""

from __future__ import annotations

import pytest
from sqlmodel import text

from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import (
    TASK_STATUS_ARCHIVED, TASK_STATUS_ARMED, TASK_STATUS_DONE,
    TASK_STATUS_FAILED, TASK_STATUS_IDEA, TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
)
from lib.pipeline import overview as overview_module


def _repo(repo_id: int, name: str):
    with SessionLocal() as session:
        session.add(Repo(id=repo_id, name=name, path=f"/tmp/{name}"))
        session.commit()


def _priority(task_id: int, rank: int):
    with SessionLocal() as session:
        session.exec(text("UPDATE agent_tasks SET priority_rank = :r"
                          " WHERE id = :i").bindparams(r=rank, i=task_id))
        session.commit()


def _overview(client, project=None) -> dict:
    url = "/api/pipeline/overview" + (f"?project={project}" if project else "")
    response = client.get(url)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _loose_ids(client, project=None) -> list[int]:
    return [r["id"] for r in _overview(client, project)["loose_tasks"]["rows"]]


@pytest.fixture
def seeded(native_task, feature_row):
    """#A loose in project 7, #B loose in no project, #C a feature's idea."""
    _repo(7, "core")
    feature = feature_row("f")
    return {"A": native_task(title="A", project_id=7),
            "B": native_task(title="B"),
            "C": native_task(title="C", feature_id=feature.id)}


def test_overview_lists_open_loose_tasks(flask_client, seeded):
    panel = _overview(flask_client)["loose_tasks"]

    assert panel["total"] == 2
    assert {r["id"] for r in panel["rows"]} == {seeded["A"], seeded["B"]}
    assert set(panel["rows"][0]) == {"id", "title", "status", "priority",
                                     "kind", "project_id"}


def test_overview_loose_tasks_follow_scope(flask_client, seeded):
    assert _loose_ids(flask_client, 7) == [seeded["A"]]
    assert _loose_ids(flask_client, "none") == [seeded["B"]]


def test_settled_loose_tasks_are_not_in_the_panel(flask_client, native_task):
    native_task(title="D", status=TASK_STATUS_DONE)
    native_task(title="E", status=TASK_STATUS_ARCHIVED)

    assert _overview(flask_client)["loose_tasks"] == {"total": 0, "rows": []}


def test_loose_tasks_cap_and_order(flask_client, native_task):
    ideas = [native_task(title=f"idea {i}") for i in range(3)]
    armed = native_task(title="armed", status=TASK_STATUS_ARMED)
    queued = native_task(title="queued", status=TASK_STATUS_QUEUED)
    failed = native_task(title="failed", status=TASK_STATUS_FAILED)
    running = native_task(title="running", status=TASK_STATUS_RUNNING)
    _priority(queued, 0)
    _priority(armed, 1)

    panel = _overview(flask_client)["loose_tasks"]

    assert panel["total"] == 7
    assert [r["id"] for r in panel["rows"]] == [running, failed, queued,
                                                armed, ideas[0]]
    assert overview_module.LOOSE_TASKS_CAP == 5


@pytest.mark.parametrize("scope", [None, "7", "none"])
def test_loose_total_equals_root_only_next_step_groups(flask_client,
                                                       native_task, seeded,
                                                       scope):
    for status in (TASK_STATUS_ARMED, TASK_STATUS_RUNNING, TASK_STATUS_FAILED,
                   TASK_STATUS_DONE, TASK_STATUS_ARCHIVED):
        native_task(title=status, status=status)
        native_task(title=f"{status} 7", status=status, project_id=7)
    query = "group=next_step&root_only=1" + (f"&project_id={scope}"
                                             if scope else "")
    body = flask_client.get(f"/api/tasks?{query}").get_json()
    open_n = sum(g["n"] for g in body["groups"] if g["key"] != "done")

    assert open_n == body["open"]

    assert _overview(flask_client, scope)["loose_tasks"]["total"] == open_n


def test_no_project_row_under_scope_all(flask_client, seeded):
    row = _overview(flask_client)["projects"]["no_project"]

    assert row["scope"] == "none"
    assert row["open_tasks"] >= 1


def test_no_project_row_absent_when_bucket_is_empty(flask_client,
                                                    native_task):
    _repo(7, "core")
    native_task(title="filed", project_id=7)
    native_task(title="settled", status=TASK_STATUS_DONE)

    assert "no_project" not in _overview(flask_client)["projects"]


def test_no_project_row_only_under_scope_all(flask_client, seeded):
    assert "projects" not in _overview(flask_client, 7)
    assert "projects" not in _overview(flask_client, "none")


def test_no_project_row_counts_open_defects(flask_client, defect_row):
    defect_row(None)

    row = _overview(flask_client)["projects"]["no_project"]

    assert (row["open_tasks"], row["open_defects"]) == (0, 1)


def test_idea_rows_are_loose_only(flask_client, seeded):
    statuses = {r["status"] for r in _overview(flask_client)
                ["loose_tasks"]["rows"]}

    assert statuses == {TASK_STATUS_IDEA}
