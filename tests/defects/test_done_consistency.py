"""A fix task is never settled `done` by a run while its defect is open.

`finish_run` already refused the agent's own report (B-R1), but a clean exit,
a Stop of an idle run or a passing verify settled the task without one — task
#345 read `done` over defect #242. Every settle goes through
`_settle_and_fire`, so that is where the check lives (B-R2). The gap a human
can still open (Mark done, a reopen) is flagged on the defect row instead
(B-R4), and counted by the same join I-B2 states.
"""

from __future__ import annotations

from sqlalchemy import text

from lib.defects import store
from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, AgentTaskRun, TASK_STATUS_ARMED, TASK_STATUS_DONE,
    TASK_STATUS_RUNNING)
from lib.tasks import deps, runs
from lib.tasks import store as task_store


def _outcome(status="exited", detail="completed"):
    return type("Outcome", (), {"status": status, "detail": detail})()


def _running_fix(native_task, feature_row):
    feature = feature_row()
    defect = store.record("the list sorts oldest first", feature_id=feature.id)
    task_id = native_task(title="fix it", feature_id=feature.id)
    store.set_fix_item(defect.id, task_id)
    runs.arm(task_id, params={}, autorun=0)
    run = runs.open_attempt(task_id)
    task_store.set_status(task_id, TASK_STATUS_RUNNING)
    return feature, defect.id, task_id, run["id"]


def _armed_dependent(native_task, task_id):
    dependent = native_task(title="after the fix")
    runs.arm(dependent, params={}, autorun=0)
    task_store.set_status(dependent, TASK_STATUS_ARMED)
    deps.set_deps(dependent, [task_id])
    return dependent


def _task(task_id) -> AgentTask:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


def _run(run_id) -> AgentTaskRun:
    with SessionLocal() as session:
        return session.get(AgentTaskRun, run_id)


def test_clean_exit_with_an_open_fix_defect(native_task, feature_row,
                                            monkeypatch):
    _feature, defect_id, task_id, run_id = _running_fix(native_task,
                                                        feature_row)
    dependent = _armed_dependent(native_task, task_id)
    launched = []
    monkeypatch.setattr(runs, "start_run",
                        lambda tid, **_: launched.append(tid))

    assert runs.handle_completion(task_id, run_id, _outcome()) is None

    run = _run(run_id)
    assert run.status == "failed"
    assert run.exit_reason == f"ended with defect #{defect_id} still open"
    assert _task(task_id).status == "failed"
    assert launched == []
    assert _task(dependent).status == TASK_STATUS_ARMED
    assert store.get(defect_id).status == "open"


def test_clean_exit_after_the_defect_is_closed(native_task, feature_row):
    _feature, defect_id, task_id, run_id = _running_fix(native_task,
                                                        feature_row)
    store.close(defect_id, missing_check="test_newest_first")

    runs.handle_completion(task_id, run_id, _outcome())

    assert _run(run_id).status == "done"
    assert _task(task_id).status == TASK_STATUS_DONE


def test_stop_idle_run_with_open_defect_settles_failed(native_task,
                                                       feature_row):
    """Stopping a steerable run parked on an empty queue ends its session
    cleanly — an `exited` outcome with no report behind it (S2)."""
    _feature, defect_id, task_id, run_id = _running_fix(native_task,
                                                        feature_row)

    runs.handle_completion(task_id, run_id,
                           _outcome(status="exited", detail="stopped"))

    assert _run(run_id).status == "failed"
    assert f"#{defect_id}" in _run(run_id).exit_reason
    assert _task(task_id).status == "failed"


def test_every_open_defect_is_named_in_the_reason(native_task, feature_row):
    feature, first, task_id, run_id = _running_fix(native_task, feature_row)
    second = store.record("the header overlaps", feature_id=feature.id)
    store.set_fix_item(second.id, task_id)

    runs.handle_completion(task_id, run_id, _outcome())

    assert _run(run_id).exit_reason == (
        f"ended with defects #{first}, #{second.id} still open")


def test_a_failed_settle_is_left_as_it_was(native_task, feature_row):
    _feature, _defect_id, task_id, run_id = _running_fix(native_task,
                                                         feature_row)

    runs.handle_completion(task_id, run_id,
                           _outcome(status="failed", detail="crashed"))

    assert _run(run_id).status == "failed"
    assert _run(run_id).exit_reason == "crashed"


def test_a_done_settle_does_not_land_over_an_open_defect(
        native_task, feature_row):
    _feature, defect_id, task_id, run_id = _running_fix(native_task,
                                                        feature_row)

    runs._settle_and_fire(task_id, run_id, "done", "session exited")

    assert _run(run_id).status == "failed"
    assert _task(task_id).status == "failed"


def _drift_by_sql() -> int:
    with SessionLocal() as session:
        return session.execute(text(
            "SELECT count(*) FROM defects d JOIN agent_tasks t "
            "ON t.id = d.fix_task_id WHERE d.status = 'open' "
            "AND t.status IN ('done', 'archived')")).scalar_one()


def _list(flask_client, query=""):
    answer = flask_client.get(f"/api/defects?{query}",
                              environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert answer.status_code == 200, answer.get_json()
    return answer.get_json()


def test_drift_is_visible(flask_client, native_task, feature_row):
    feature = feature_row()
    drifted = store.record("reopened after the fix", feature_id=feature.id)
    archived = store.record("fix archived, bug open", feature_id=feature.id)
    pending = store.record("fix still running", feature_id=feature.id)
    closed = store.record("fixed and closed", feature_id=feature.id)
    for defect, status in ((drifted, "done"), (archived, "archived"),
                           (pending, TASK_STATUS_RUNNING), (closed, "done")):
        task_id = native_task(title=defect.symptom, feature_id=feature.id)
        store.set_fix_item(defect.id, task_id)
        task_store.set_status(task_id, status)
    store.close(closed.id, missing_check="test_it")
    store.record("no fix task at all", feature_id=feature.id)

    body = _list(flask_client, f"feature_id={feature.id}")

    flagged = {row["id"] for row in body["items"] if row["fix_drift"]}
    assert flagged == {drifted.id, archived.id}
    assert body["extras"]["fix_drift"] == 2 == _drift_by_sql()
    assert _list(flask_client)["extras"]["fix_drift"] == _drift_by_sql()

    one = flask_client.get(f"/api/defects/{drifted.id}",
                           environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert one.get_json()["defect"]["fix_drift"] is True


def test_the_count_covers_rows_the_page_did_not_load(flask_client,
                                                     native_task, feature_row):
    feature = feature_row()
    for n in range(3):
        defect = store.record(f"drifted {n}", feature_id=feature.id)
        task_id = native_task(title=f"fix {n}", feature_id=feature.id)
        store.set_fix_item(defect.id, task_id)
        task_store.set_status(task_id, "done")

    body = _list(flask_client, f"feature_id={feature.id}&size=1")

    assert len(body["items"]) == 1
    assert body["extras"]["fix_drift"] == 3
