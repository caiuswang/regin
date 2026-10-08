"""The Overview endpoint and the Projects attention sort (feature #110, S1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import text

from lib.features import events
from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import (
    AgentTaskDep, AgentTaskRun, DEP_KIND_AFTER, RUN_STATUS_RUNNING, TASK_STATUS_ARMED, TASK_STATUS_DONE,
    TASK_STATUS_FAILED, TASK_STATUS_RUNNING,
)
from lib.pipeline import overview as overview_module
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
    for status in (TASK_STATUS_ARMED, TASK_STATUS_RUNNING,
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
        ["Ideas", "Armed", "Running", "Done", "Failed"],
        ["Spec", "Building", "Shipped", "Escaped"],
        ["Triage", "Open", "Closed"]]
    # Every task but archived, a feature's step included: the Tasks tab's own
    # population.
    assert stages["Tasks"]["total"] == 6
    # Abandoned is never shown, so it is neither a part nor in the total.
    assert stages["Features"]["total"] == 3
    assert [p["n"] for p in stages["Defects"]["parts"]] == [1, 1, 1]
    highlighted = [p["label"] for stage in stages.values()
                   for p in stage["parts"] if p["highlighted"]]
    assert highlighted == ["Escaped", "Failed", "Triage"]


def test_overview_run_next_orders_dependency_clear_first(flask_client,
                                                         native_task):
    running = native_task(title="#408 running", status=TASK_STATUS_RUNNING)
    waiting = native_task(title="#409 waits", status=TASK_STATUS_ARMED)
    _depends(waiting, running)
    _priority(waiting, "urgent", 0)
    low = native_task(title="low ready", status=TASK_STATUS_ARMED)
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
    assert (run_next["total"], run_next["ready"], run_next["blocked"]) \
        == (4, 3, 1)
    assert [r["action"]["kind"] for r in rows] == ["run", "run", "run", "wait"]
    assert rows[-1]["action"] == {"kind": "wait", "on": running,
                                  "on_status": TASK_STATUS_RUNNING}


def test_overview_run_next_empty_is_a_panel_not_a_gap(flask_client):
    run_next = _overview(flask_client)["run_next"]

    assert run_next == {"total": 0, "ready": 0, "blocked": 0, "rows": []}


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
    assert scoped["run_next"]["ready"] == 1


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
    # Completed projects count as quiet: "done" and "calm" are the two.
    assert (panel["active"], panel["quiet"], panel["total"]) == (4, 2, 6)
    assert (first["escaped"], first["open_defects"],
            first["stage_bar"]["escaped"]) == (1, 1, 1)


def test_projects_panel_caps_rows_and_counts_the_rest(tmp_db, native_task):
    for repo_id in range(1, 9):
        _repo(repo_id, f"p{repo_id}")
        native_task(title=f"t{repo_id}", status=TASK_STATUS_ARMED,
                    project_id=repo_id)

    panel = projects.panel()

    assert (len(panel["rows"]), panel["active"], panel["quiet"]) == (
        projects.PANEL_ROWS, 8, 0)


def test_overview_payload_has_no_escaped_list(flask_client, feature_row,
                                              defect_row):
    """The Escaped panel is gone: an escape is a loop part, not a list."""
    _repo(1, "core")
    for title in ("walked back", "also walked back"):
        row = feature_row(title, stage=events.SHIPPED)
        _file_feature(row.id, 1)
        defect_row(row.id)

    for scope in (None, "none", "1"):
        body = _overview(flask_client, scope)
        assert "escaped" not in body
        assert "hint" not in _stages(body)["Tasks"]
    escaped = {p["key"]: p["n"]
               for p in _stages(_overview(flask_client))["Features"]["parts"]}
    assert escaped["escaped"] == 2
    panel = _overview(flask_client)["projects"]
    assert set(panel) == {"sort", "total", "active", "quiet", "rows"}
    assert (panel["active"], panel["quiet"], panel["total"]) == (1, 0, 1)


def _live_attempt(task_id: int, started_at: str, trace_id=None, attempt=1):
    with SessionLocal() as session:
        session.add(AgentTaskRun(task_id=task_id, trace_id=trace_id,
                                 attempt=attempt, status=RUN_STATUS_RUNNING,
                                 started_at=started_at))
        session.commit()


def _session_with_turns(trace_id: str, turns: int, cost_usd):
    with SessionLocal() as session:
        session.exec(text(
            "INSERT INTO sessions (trace_id, started_at, last_seen, cost_usd)"
            " VALUES (:t, '2026-09-27 10:00:00', '2026-09-27 10:00:00', :c)")
            .bindparams(t=trace_id, c=cost_usd))
        for i in range(turns):
            session.exec(text(
                "INSERT INTO turn_usage (trace_id, turn_uuid, turn_index,"
                " timestamp) VALUES (:t, :u, :i, '2026-09-27 10:00:00')")
                .bindparams(t=trace_id, u=f"{trace_id}-{i}", i=i))
        session.commit()


def test_overview_running_now_lists_live_attempts_newest_first(
        flask_client, native_task):
    older = native_task(title="index spans", status=TASK_STATUS_RUNNING)
    _live_attempt(older, "2026-09-27 09:00:00", trace_id="tr-older")
    _session_with_turns("tr-older", 3, 2.1)
    newer = native_task(title="reconnect tail", status=TASK_STATUS_RUNNING)
    _live_attempt(newer, "2026-09-27 09:30:00", attempt=2)
    fresh = native_task(title="just started", status=TASK_STATUS_RUNNING)
    _live_attempt(fresh, "2026-09-27 09:45:00", trace_id="tr-fresh")
    native_task(title="armed, not running", status=TASK_STATUS_ARMED)

    running = _overview(flask_client)["running"]
    rows = running["rows"]

    assert running["total"] == 3
    assert [r["id"] for r in rows] == [fresh, newer, older]
    # A trace with no ingested turns yet reads as unknown, not as zero.
    assert (rows[0]["turns"], rows[0]["cost_usd"]) == (None, None)
    assert rows[1]["live"]["attempt"] == 2
    assert (rows[1]["turns"], rows[1]["cost_usd"]) == (None, None)
    assert rows[2]["live"]["trace_id"] == "tr-older"
    assert (rows[2]["turns"], rows[2]["cost_usd"]) == (3, 2.1)


def test_overview_running_now_caps_rows_and_follows_scope(flask_client,
                                                          native_task):
    _repo(7, "regin-core")
    for i in range(overview_module.RUNNING_CAP + 1):
        native_task(title=f"loose {i}", status=TASK_STATUS_RUNNING)
    filed = native_task(title="core", status=TASK_STATUS_RUNNING,
                        project_id=7)

    everywhere = _overview(flask_client)["running"]
    scoped = _overview(flask_client, 7)["running"]

    assert everywhere["total"] == overview_module.RUNNING_CAP + 2
    assert len(everywhere["rows"]) == overview_module.RUNNING_CAP
    assert [r["id"] for r in scoped["rows"]] == [filed]
    assert _overview(flask_client, "none")["running"]["total"] == (
        overview_module.RUNNING_CAP + 1)


def test_overview_running_now_empty(flask_client):
    assert _overview(flask_client)["running"] == {"total": 0, "rows": []}


def test_triage_queue_ignores_project_scope(flask_client, core_project,
                                            defect_row):
    unattributed = defect_row(None, symptom="nobody routed this")
    queue_url = "/api/defects?scope=triage&sort=opened:desc"

    for scope in (7, "none", None):
        overview = _overview(flask_client, scope)["needs_decision"]
        suffix = f"&project={scope}" if scope is not None else ""
        listed = flask_client.get(queue_url + suffix).get_json()

        assert [r["symptom"] for r in overview["rows"]] == [unattributed]
        assert [r["symptom"] for r in listed["items"]] == [unattributed]
        assert overview["waiting"] == listed["pagination"]["total"] == 1
        assert overview["all_projects"] is (scope == 7)
        assert "project" not in listed["applied"]["filters"]


def _finished(task_id: int, days_ago: float):
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%d %H:%M:%S")
    with SessionLocal() as session:
        session.exec(text("UPDATE agent_tasks SET updated_at = :u WHERE id = :i")
                     .bindparams(u=stamp, i=task_id))
        session.commit()


def test_recently_done_honours_project_scope_and_cap(flask_client,
                                                     native_task):
    _repo(7, "regin-core")
    filed = []
    for days in (3, 1, 2):
        task_id = native_task(title=f"core {days}d", status=TASK_STATUS_DONE,
                              project_id=7)
        _finished(task_id, days)
        filed.append((days, task_id))
    for i in range(overview_module.RECENTLY_DONE_CAP):
        _finished(native_task(title=f"loose {i}", status=TASK_STATUS_DONE),
                  0.5 + i / 10)
    _finished(native_task(title="core, long ago", status=TASK_STATUS_DONE,
                          project_id=7), 10)
    native_task(title="core armed", status=TASK_STATUS_ARMED, project_id=7)

    everywhere = _overview(flask_client)["recently_done"]
    scoped = _overview(flask_client, 7)["recently_done"]
    unfiled = _overview(flask_client, "none")["recently_done"]

    assert everywhere["week"] == overview_module.RECENTLY_DONE_CAP + 3
    assert len(everywhere["rows"]) == overview_module.RECENTLY_DONE_CAP
    assert scoped["week"] == 3
    assert [r["id"] for r in scoped["rows"]] == [
        task_id for _, task_id in sorted(filed)]
    assert unfiled["week"] == overview_module.RECENTLY_DONE_CAP


def test_recently_done_empty(flask_client, native_task):
    native_task(title="armed", status=TASK_STATUS_ARMED)
    assert _overview(flask_client)["recently_done"] == {"week": 0, "rows": []}


def _active_at(feature_id: int, at: str):
    with SessionLocal() as session:
        session.exec(text("UPDATE feature_events SET at = :a"
                          " WHERE feature_id = :i").bindparams(a=at, i=feature_id))
        session.commit()


def test_overview_loop_is_feature_first(flask_client, native_task,
                                        feature_row, defect_row):
    _seed_everything(native_task, feature_row, defect_row)

    for scope in (None, "none"):
        loop = _overview(flask_client, scope)["loop"]
        assert [s["label"] for s in loop["stages"]] == [
            "Features", "Tasks", "Defects"]
        assert "fix_tasks" in loop


def test_features_in_flight_total_matches_loop(flask_client, native_task,
                                               feature_row, defect_row,
                                               core_project):
    _seed_everything(native_task, feature_row, defect_row)
    old = feature_row("old spec")
    newest = feature_row("newest build", stage=events.BUILD_STARTED)
    _active_at(old.id, "2020-01-01 00:00:00")
    _active_at(newest.id, "2099-01-01 00:00:00")

    for scope in (None, "none", 7):
        body = _overview(flask_client, scope)
        parts = {p["key"]: p["n"]
                 for p in _stages(body)["Features"]["parts"]}
        flight = body["features_in_flight"]
        assert flight["total"] == parts["spec"] + parts["building"]
        assert len(flight["rows"]) == min(flight["total"], 5)
        assert {r["stage"] for r in flight["rows"]} <= {"spec", "building"}
        stamps = [r["last_activity_at"] for r in flight["rows"]]
        assert stamps == sorted(stamps, reverse=True)

    everything = _overview(flask_client)["features_in_flight"]
    assert everything["total"] == 11
    assert everything["rows"][0]["id"] == newest.id
    assert old.id not in [r["id"] for r in everything["rows"]]
    scoped = _overview(flask_client, 7)["features_in_flight"]
    assert scoped["total"] == 6
    assert {r["project_id"] for r in scoped["rows"]} == {7}
    assert set(everything["rows"][0]) == {
        "id", "title", "stage", "project_id", "last_activity_at"}
