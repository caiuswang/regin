"""A fix task cannot be reported done over the defect it was cut to fix.

Task #206 settled `done` while defect #179 stayed open: nothing in its launch
prompt said to close the defect, and nothing at finish noticed it had not been.
Closing stays the agent's act — `close` needs the check that would have caught
the bug — so the run is told the exact command, and a done finish is refused
until it has been run.
"""

from __future__ import annotations

from lib.comments import store as comments
from lib.defects import store
from lib.orm import SessionLocal
from lib.orm.models.comments import SUBJECT_TASK
from lib.orm.models.tasks import AgentTask, TASK_STATUS_RUNNING
from lib.tasks import runs
from lib.tasks import store as task_store


def _running_fix(native_task, feature_row):
    feature = feature_row()
    defect = store.record("the list sorts oldest first", feature_id=feature.id)
    task_id = native_task(title="fix it", feature_id=feature.id)
    store.set_fix_item(defect.id, task_id)
    runs.arm(task_id, params={}, verify_cmd=None, autorun=0)
    run = runs.open_attempt(task_id)
    task_store.set_status(task_id, TASK_STATUS_RUNNING)
    return defect.id, task_id, run["id"]


def _finish(flask_client, run_id, **body):
    return flask_client.post(f"/api/tasks/runs/{run_id}/finish", json=body,
                             environ_base={"REMOTE_ADDR": "127.0.0.1"})


def _task_status(task_id):
    with SessionLocal() as session:
        return session.get(AgentTask, task_id).status


def test_the_finish_block_names_the_close_command_for_each_open_defect(
        native_task, feature_row):
    defect_id, task_id, run_id = _running_fix(native_task, feature_row)

    block = runs._finish_block(run_id, task_id=task_id)

    assert runs._close_defect_command(defect_id) in block
    assert f"defect #{defect_id}" in block
    assert block.index("defects close") < block.index(runs._finish_command(run_id))


def test_a_task_no_defect_points_at_is_told_nothing_about_defects(
        native_task, feature_row):
    task_id = native_task(title="plain work")
    run = runs.open_attempt(task_id)

    assert "defects close" not in runs._finish_block(run["id"], task_id=task_id)


def test_a_closed_defect_drops_out_of_the_finish_block(native_task,
                                                       feature_row):
    defect_id, task_id, run_id = _running_fix(native_task, feature_row)
    store.close(defect_id, missing_check="test_newest_first")

    assert "defects close" not in runs._finish_block(run_id, task_id=task_id)


def test_done_is_refused_while_the_defect_is_open(flask_client, native_task,
                                                  feature_row):
    defect_id, task_id, run_id = _running_fix(native_task, feature_row)

    answer = _finish(flask_client, run_id, status="done", reason="fixed it")

    assert answer.status_code == 409, answer.get_json()
    body = answer.get_json()
    assert body["open_defects"] == [defect_id]
    assert f"defects close {defect_id}" in body["error"]
    assert _task_status(task_id) == TASK_STATUS_RUNNING
    assert store.get(defect_id).status == "open"
    assert comments.thread_with_comments(SUBJECT_TASK, str(task_id)) == []


def test_done_settles_once_the_defect_is_closed(flask_client, native_task,
                                                feature_row):
    defect_id, task_id, run_id = _running_fix(native_task, feature_row)
    store.close(defect_id, missing_check="test_newest_first")

    answer = _finish(flask_client, run_id, status="done", reason="fixed it")

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["settled"] is True
    assert _task_status(task_id) == "done"


def test_failed_is_never_refused(flask_client, native_task, feature_row):
    """A run that could not fix the bug must still be able to say so."""
    defect_id, task_id, run_id = _running_fix(native_task, feature_row)

    answer = _finish(flask_client, run_id, status="failed", reason="blocked")

    assert answer.status_code == 200, answer.get_json()
    assert store.get(defect_id).status == "open"
