"""The Overview endpoint and the Projects attention sort (feature #110, S1)."""

from __future__ import annotations

import pytest
from sqlmodel import text

from lib.features import events
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import (
    AgentTaskDep, DEP_KIND_AFTER, TASK_STATUS_ARMED, TASK_STATUS_DONE,
    TASK_STATUS_FAILED, TASK_STATUS_QUEUED, TASK_STATUS_RUNNING,
)
from lib.pipeline import projects


def _file_feature(feature_id: int, project_id):
    with SessionLocal() as session:
        session.exec(text("UPDATE features SET project_id = :p WHERE id = :i")
                     .bindparams(p=project_id, i=feature_id))
        session.commit()


def _depends(task_id: int, on: int, kind: str = "requires"):
    with SessionLocal() as session:
        session.add(AgentTaskDep(task_id=task_id, depends_on_id=on, kind=kind))
        session.commit()


def _priority(task_id: int, priority: str, rank: int):
    with SessionLocal() as session:
        session.exec(text("UPDATE agent_tasks SET priority = :p,"
                          " priority_rank = :r WHERE id = :i")
                     .bindparams(p=priority, r=rank, i=task_id))
        session.commit()


def _repo(repo_id: int, name: str):
    with SessionLocal() as session:
        session.add(Repo(id=repo_id, name=name, path=f"/tmp/{name}"))
        session.commit()


def _overview(client, project=None):
    url = "/api/pipeline/overview" + (f"?project={project}" if project else "")
    response = client.get(url)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _stages(body) -> dict:
    return {stage["label"]: stage for stage in body["loop"]["stages"]}


def _seed_everything(native_task, feature_row, defect_row):
    for status in (TASK_STATUS_ARMED, TASK_STATUS_QUEUED, TASK_STATUS_RUNNING,
                   TASK_STATUS_DONE, TASK_STATUS_FAILED, "idea", "archived"):
        native_task(title=f"loose {status}", status=status)
    building = feature_row("building", stage=events.BUILD_STARTED)
    native_task(title="a step", status=TASK_STATUS_ARMED,
                feature_id=building.id)
    feature_row("spec")
    shipped = feature_row("escaped", stage=events.SHIPPED)
    defect_row(shipped.id)
    defect_row(shipped.id, status="closed")
    feature_row("taken down", stage=events.ABANDONED)
    defect_row(None)


def _labels(stage) -> list[str]:
    return [p["label"] for p in stage["parts"]]


def _assert_loop_agrees_with_tab_counts(client, scope):
    stages = _stages(_overview(client, scope))
    counts = client.get("/api/pipeline/counts" + (
        f"?project={scope}" if scope else "")).get_json()["counts"]
    sums = {label: sum(p["n"] for p in stage["parts"])
            for label, stage in stages.items()}
    totals = {label: stage["total"] for label, stage in stages.items()}
    assert totals == sums
    assert totals == {"Tasks": counts["tasks"],
                      "Features": counts["features"],
                      "Defects": counts["defects"]}


def test_overview_loop_parts_sum_to_totals(flask_client, native_task,
                                           feature_row, defect_row):
    _seed_everything(native_task, feature_row, defect_row)

    for scope in (None, "none"):
        _assert_loop_agrees_with_tab_counts(flask_client, scope)

    stages = _stages(_overview(flask_client))
    assert [_labels(stages[k]) for k in ("Tasks", "Features", "Defects")] == [
        ["Ideas", "Armed", "Queued", "Running", "Done", "Failed"],
        ["Spec", "Building", "Shipped", "Escaped"],
        ["Triage", "Open", "Closed"]]
    # Loose tasks only, archived out: the Tasks tab's own population.
    assert stages["Tasks"]["total"] == 6
    # Abandoned is never shown, so it is neither a part nor in the total.
    assert stages["Features"]["total"] == 3
    assert [p["n"] for p in stages["Defects"]["parts"]] == [1, 1, 1]
    highlighted = [p["label"] for stage in stages.values()
                   for p in stage["parts"] if p["highlighted"]]
    assert highlighted == ["Failed", "Escaped", "Triage"]


def test_overview_run_next_orders_dependency_clear_first(flask_client,
                                                         native_task):
    running = native_task(title="#408 running", status=TASK_STATUS_RUNNING)
    waiting = native_task(title="#409 waits", status=TASK_STATUS_ARMED)
    _depends(waiting, running)
    _priority(waiting, "urgent", 0)
    low = native_task(title="low ready", status=TASK_STATUS_QUEUED)
    _priority(low, "low", 3)
    high = native_task(title="high ready", status=TASK_STATUS_ARMED)
    _priority(high, "high", 1)
    settled = native_task(title="dep done", status=TASK_STATUS_DONE)
    after_ok = native_task(title="after a failure", status=TASK_STATUS_ARMED)
    failed = native_task(title="failed dep", status=TASK_STATUS_FAILED)
    _depends(after_ok, failed, DEP_KIND_AFTER)
    _depends(after_ok, settled)
    native_task(title="an idea")

    run_next = _overview(flask_client)["run_next"]
    rows = run_next["rows"]

    # `after` over a failed task is satisfied; priority "none" ranks last.
    assert [r["id"] for r in rows] == [high, low, after_ok, waiting]
    assert (run_next["total"], run_next["ready"]) == (4, 3)
    assert [r["action"]["kind"] for r in rows] == ["run", "run", "run", "wait"]
    assert rows[-1]["action"] == {"kind": "wait", "on": running,
                                  "on_status": TASK_STATUS_RUNNING}


def test_overview_run_next_empty_is_a_panel_not_a_gap(flask_client):
    run_next = _overview(flask_client)["run_next"]

    assert run_next == {"total": 0, "ready": 0, "rows": []}


@pytest.fixture
def core_project(native_task, feature_row, defect_row):
    """Six features and one armed task filed under repo 7, plus one of each
    filed nowhere. Returns the unfiled feature's id."""
    _repo(7, "regin-core")
    filed = [feature_row(f"core {i}") for i in range(6)]
    for row in filed:
        _file_feature(row.id, 7)
    defect_row(filed[0].id)
    native_task(title="core task", status=TASK_STATUS_ARMED, project_id=7)
    native_task(title="other task", status=TASK_STATUS_ARMED)
    return feature_row("elsewhere").id


def _panels(client, scope) -> set:
    return {"projects", "project_head", "project_features"} & set(
        _overview(client, scope))


def test_overview_project_scope_replaces_projects_panel(flask_client,
                                                        core_project):
    head = _overview(flask_client, 7)["project_head"]

    assert _panels(flask_client, None) == {"projects"}
    assert _panels(flask_client, 7) == {"project_head", "project_features"}
    assert _panels(flask_client, "none") == set()
    assert head["breadcrumb"] == ["All projects", "regin-core"]
    assert (head["open_defects"], head["open_tasks"]) == (1, 1)
    assert sum(head["stage_bar"].values()) == 6


def test_overview_project_scope_filters_every_panel(flask_client,
                                                    core_project):
    scoped = _overview(flask_client, 7)
    features = scoped["project_features"]
    listed = [r["id"] for r in features["rows"]]

    assert (features["total"], len(listed)) == (6, 5)
    assert core_project not in listed
    assert [r["title"] for r in scoped["run_next"]["rows"]] == ["core task"]
    assert _stages(scoped)["Tasks"]["hint"] == "1 ready · 0 running"


def test_overview_refuses_an_unknown_scope(flask_client):
    statuses = [flask_client.get(f"/api/pipeline/overview?project={p}")
                .status_code for p in ("999", "bogus")]

    assert statuses == [404, 400]


def test_projects_attention_sort(flask_client, native_task, feature_row,
                                 defect_row):
    """escapes desc → open defects desc → last activity desc."""
    for repo_id, name in ((1, "calm"), (2, "escaped"), (3, "buggy"),
                          (4, "busy-new"), (5, "busy-old"), (6, "done")):
        _repo(repo_id, name)
    shipped = feature_row("shipped then blamed", stage=events.SHIPPED)
    _file_feature(shipped.id, 2)
    defect_row(shipped.id)
    buggy = [feature_row(f"buggy {i}", stage=events.BUILD_STARTED)
             for i in range(2)]
    for row in buggy:
        _file_feature(row.id, 3)
        defect_row(row.id)
    old = native_task(title="old", status=TASK_STATUS_ARMED, project_id=5)
    new = native_task(title="new", status=TASK_STATUS_ARMED, project_id=4)
    with SessionLocal() as session:
        session.exec(text("UPDATE agent_tasks SET updated_at = :u WHERE id = :i")
                     .bindparams(u="2020-01-01 00:00:00", i=old))
        session.exec(text("UPDATE agent_tasks SET updated_at = :u WHERE id = :i")
                     .bindparams(u="2099-01-01 00:00:00", i=new))
        session.commit()
    finished = feature_row("finished", stage=events.SHIPPED)
    _file_feature(finished.id, 6)

    listed = flask_client.get(
        "/api/pipeline/projects?sort=attention").get_json()
    panel = _overview(flask_client)["projects"]
    first = panel["rows"][0]

    # The listing hides nothing: the finished project ranks by its activity.
    assert listed["applied"]["sort"] == "attention:desc"
    assert [row["name"] for row in listed["items"]][:3] == [
        "escaped", "buggy", "busy-new"]
    assert [row["name"] for row in panel["rows"]] == [
        "escaped", "buggy", "busy-new", "busy-old"]
    assert (panel["completed_hidden"], panel["quiet"], panel["total"]) == (
        1, 1, 6)
    assert (first["escaped"], first["open_defects"],
            first["stage_bar"]["escaped"]) == (1, 1, 1)


def test_projects_panel_caps_rows_and_counts_the_rest(tmp_db, native_task):
    for repo_id in range(1, 9):
        _repo(repo_id, f"p{repo_id}")
        native_task(title=f"t{repo_id}", status=TASK_STATUS_ARMED,
                    project_id=repo_id)

    panel = projects.panel()

    assert (len(panel["rows"]), panel["more"], panel["quiet"]) == (
        projects.PANEL_ROWS, 3, 0)


def test_escaped_panel_is_uncapped_and_newest_escape_first(
        flask_client, monkeypatch, feature_row, defect_row):
    from lib.pipeline import overview as overview_module

    monkeypatch.setattr(overview_module, "MAX_PAGE_SIZE", 2)
    for day in ("03", "01", "05", "02", "04"):
        row = feature_row(f"escaped {day}", stage=events.SHIPPED)
        defect_row(row.id, opened_at=f"2026-09-{day} 10:00:00")

    titles = [e["title"] for e in _overview(flask_client)["escaped"]]

    assert titles == ["escaped 05", "escaped 04", "escaped 03", "escaped 02",
                      "escaped 01"]
