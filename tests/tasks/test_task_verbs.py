"""Task verbs by state (feature #119 S4 — R4a, D5)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models.defects import STATUS_OPEN, Defect
from lib.orm.models.tasks import AgentTask, AgentTaskDep, AgentTaskRun
from lib.tasks import runs


class _Handle:
    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id

    def add_done_callback(self, callback) -> None:
        self.callback = callback


@pytest.fixture
def launcher(monkeypatch):
    state = SimpleNamespace(calls=[])

    def _fake(prompt, **kwargs):
        state.calls.append(kwargs)
        return _Handle(f"sdk-verb{len(state.calls)}")

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return state


def _capture(client, title="a task") -> int:
    resp = client.post("/api/tasks", json={"title": title})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["task"]["id"]


def _arm(client, task_id, **params):
    resp = client.post(f"/api/tasks/{task_id}/arm", json=params)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _depends(task_id: int, on: int) -> None:
    with SessionLocal() as session:
        session.add(AgentTaskDep(task_id=task_id, depends_on_id=on,
                                 kind="requires"))
        session.commit()


def _status(task_id: int) -> str:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id).status


def _run_rows(task_id: int) -> list[AgentTaskRun]:
    with SessionLocal() as session:
        return list(session.exec(
            select(AgentTaskRun).where(AgentTaskRun.task_id == task_id)))


def _groups(client) -> dict:
    body = client.get("/api/tasks?group=next_step").get_json()
    return body, {g["key"]: g for g in body["groups"]}


def test_next_step_failed_group_and_retry(flask_client, native_task,
                                          task_run_row, launcher):
    older = native_task(title="failed long ago", status="failed")
    task_run_row(older, status="failed", ended_at="2026-09-01 09:00:00")
    newer = native_task(title="failed just now", status="failed")
    task_run_row(newer, status="failed", ended_at="2026-09-02 09:00:00")
    with SessionLocal() as session:
        row = session.exec(select(AgentTaskRun)
                           .where(AgentTaskRun.task_id == newer)).one()
        row.params_json = '{"model": "sonnet"}'
        session.add(row)
        session.commit()
    native_task(title="done", status="done")
    native_task(title="an idea")

    body, groups = _groups(flask_client)

    assert [g["key"] for g in body["groups"]] == [
        "ready", "running", "blocked", "failed", "ideas"]
    failed = groups["failed"]
    assert [r["id"] for r in failed["rows"]] == [newer, older]
    assert {r["action"] for r in failed["rows"]} == {"retry"}
    assert failed["rows"][0]["menu"] == ["arm", "mark_done", "archive",
                                         "delete"]
    assert body["done_hidden"] == 1

    # Retry is Run: the attempt spends the last attempt's parameters.
    retried = flask_client.post(f"/api/tasks/{newer}/run")
    assert retried.status_code == 201, retried.get_json()
    assert retried.get_json()["run"]["attempt"] == 2
    assert retried.get_json()["run"]["params"] == {"model": "sonnet"}
    assert _status(newer) == "running"


def test_task_disarm_returns_to_idea_and_drops_pending_run(flask_client):
    task_id = _capture(flask_client)
    _arm(flask_client, task_id, model="sonnet")
    assert [r.status for r in _run_rows(task_id)] == ["armed"]

    resp = flask_client.post(f"/api/tasks/{task_id}/disarm")

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["task"]["status"] == "idea"
    assert body["armed"] is None
    assert body["task"]["action"] == "arm"
    assert _run_rows(task_id) == []



def test_task_disarm_refuses_what_is_not_armed_or_still_running(
        flask_client, native_task, task_run_row):
    idea = _capture(flask_client)
    again = flask_client.post(f"/api/tasks/{idea}/disarm")
    assert again.status_code == 409
    assert "not 'armed'" in again.get_json()["error"]

    running = native_task(title="going", status="running")
    task_run_row(running, trace_id="t-going", status="running", ended_at=None)
    refused = flask_client.post(f"/api/tasks/{running}/disarm")
    assert refused.status_code == 409
    assert "stop it first" in refused.get_json()["error"]
    assert _status(running) == "running"



def test_task_disarm_keeps_launched_attempts(flask_client, task_run_row):
    settled = _capture(flask_client, "settled once")
    task_run_row(settled, status="failed")
    _arm(flask_client, settled)

    assert flask_client.post(f"/api/tasks/{settled}/disarm").status_code == 200
    assert [r.status for r in _run_rows(settled)] == ["failed"]
    assert flask_client.post("/api/tasks/99999/disarm").status_code == 404


def test_task_mark_done_fix_task_names_open_defects(flask_client, native_task,
                                                   defect_row):
    fix = native_task(title="fix it", status="armed")
    defect_row(symptom="breaks")
    defect_row(symptom="broke once", status="closed")
    with SessionLocal() as session:
        open_id, closed_id = session.exec(
            select(Defect.id).order_by(Defect.id)).all()
        for defect_id in (open_id, closed_id):
            session.get(Defect, defect_id).fix_task_id = fix
        session.commit()

    detail = flask_client.get(f"/api/tasks/{fix}").get_json()["task"]
    assert detail["fixes_defects"] == [open_id, closed_id]
    assert detail["fixes_open_defects"] == [open_id]
    assert "mark_done" in detail["menu"]

    resp = flask_client.patch(f"/api/tasks/{fix}", json={"status": "done"})

    assert resp.status_code == 200, resp.get_json()
    task = resp.get_json()["task"]
    assert task["status"] == "done"
    assert task["fixes_open_defects"] == [open_id]
    with SessionLocal() as session:
        assert session.get(Defect, open_id).status == STATUS_OPEN


def test_task_mark_done_releases_armed_dependents(flask_client, launcher):
    manual = _capture(flask_client, "MANUAL restart")
    _arm(flask_client, manual)
    waiting = _capture(flask_client, "builds on it")
    _depends(waiting, manual)
    _arm(flask_client, waiting, model="sonnet")
    assert launcher.calls == []

    resp = flask_client.patch(f"/api/tasks/{manual}", json={"status": "done"})

    assert resp.status_code == 200, resp.get_json()
    assert _status(manual) == "done"
    # The pending attempt of the task marked done is withdrawn, never spent.
    assert _run_rows(manual) == []
    assert _status(waiting) == "running"
    assert len(launcher.calls) == 1
    assert launcher.calls[0]["model"] == "sonnet"


def _verbs_on_every_read(client, task_id: int, status: str) -> dict:
    """`{surface: (action, menu)}` for every read that draws this task; the
    graph node carries the action alone, and archived is on no board."""
    def pair(row):
        return row["action"], tuple(row["menu"])

    reads = {"detail": pair(client.get(f"/api/tasks/{task_id}")
                            .get_json()["task"])}
    listed = client.get(f"/api/tasks?status={status}").get_json()["items"]
    reads["list"] = pair(next(r for r in listed if r["id"] == task_id))
    board = client.get(f"/api/tasks/board?column={status}")
    if board.status_code == 200:
        reads["board"] = pair(board.get_json()["columns"][0]["cards"][0])
    nodes = client.get("/api/tasks/graph?lanes=1&open=0").get_json()["nodes"]
    for node in nodes:
        if node["id"] == task_id:
            reads["graph"] = node["action"]
    return reads


@pytest.mark.parametrize(("status", "blocked", "primary", "menu"), [
    ("idea", False, "arm", ["edit", "deps", "mark_done", "archive", "delete"]),
    ("armed", False, "run", ["disarm", "arm", "edit", "deps", "mark_done",
                             "archive", "delete"]),
    ("armed", True, None, ["disarm", "arm", "deps", "edit", "archive",
                           "delete"]),
    ("running", False, "watch", ["interrupt"]),
    ("failed", False, "retry", ["arm", "mark_done", "archive", "delete"]),
    ("done", False, None, ["reopen", "archive"]),
    ("archived", False, None, ["restore", "delete"]),
])
def test_every_row_read_carries_the_r4a_verbs(flask_client, native_task,
                                              status, blocked, primary, menu):
    task_id = native_task(title="t", status=status)
    if blocked:
        _depends(task_id, native_task(title="dep"))

    reads = _verbs_on_every_read(flask_client, task_id, status)

    assert reads.pop("graph", primary) == primary
    assert set(reads.values()) == {(primary, tuple(menu))}


def test_queued_not_offered(flask_client, native_task):
    native_task(title="armed", status="armed")
    legacy = native_task(title="legacy", status="queued")
    row = flask_client.get(f"/api/tasks/{legacy}").get_json()["task"]
    assert (row["action"], row["menu"]) == (None, [])

    board = flask_client.get("/api/tasks/board").get_json()
    overview = flask_client.get("/api/pipeline/overview").get_json()
    body, _ = _groups(flask_client)

    assert "queued" not in [c["status"] for c in board["columns"]]
    tasks_stage = next(s for s in overview["loop"]["stages"]
                       if s["label"] == "Tasks")
    assert "queued" not in [p["key"] for p in tasks_stage["parts"]]
    copy = " ".join(f'{g["label"]} {g["hint"]}' for g in body["groups"])
    assert "queued" not in copy.lower()
