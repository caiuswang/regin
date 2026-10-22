"""Feature #167 Rules B — every `max_concurrent_runs` slot is named.

`GET /api/agent-runs/slots` lists who holds the pool, and a Run refused for
capacity carries the same list, so the operator sees what to close instead of
a bare "max_concurrent_runs reached".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lib.agent_sdk import registry
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask, AgentTaskRun
from lib.settings import settings


def _runner(cwd: str, *, working: bool = False) -> SimpleNamespace:
    return SimpleNamespace(cwd=cwd, stop_requested=False, retrying=None,
                           turn_in_flight=working,
                           delegated_work_in_flight=False)


def _task_with_attempt(trace_id: str, status: str) -> int:
    with SessionLocal() as session:
        task = AgentTask(title="finished work", status=status, source="web")
        session.add(task)
        session.commit()
        session.refresh(task)
        session.add(AgentTaskRun(task_id=task.id, trace_id=trace_id,
                                 attempt=1, status="exited",
                                 started_at="2026-10-22 00:00:00"))
        session.commit()
        return task.id


@pytest.fixture
def live_runs():
    ids = []

    def _register(trace_id, runner):
        registry.register_run(trace_id, runner)
        ids.append(trace_id)

    yield _register
    for trace_id in ids:
        registry.unregister_run(trace_id)


@pytest.fixture
def pool_of_two(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "max_concurrent_runs", 2)
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)


def test_slots_endpoint_lists_every_holder(flask_client, live_runs,
                                           pool_of_two):
    done_id = _task_with_attempt("sdk-idle-done", "done")
    live_runs("sdk-idle-done", _runner("/repo/a"))
    live_runs("sdk-live-chat", _runner("/repo/b", working=True))

    body = flask_client.get("/api/agent-runs/slots").get_json()

    assert (body["used"], body["limit"]) == (2, 2)
    assert body["used"] == registry.active_run_count() == len(body["holders"])
    by_trace = {h["trace_id"]: h for h in body["holders"]}
    assert by_trace["sdk-idle-done"] == {
        "trace_id": "sdk-idle-done", "phase": "idle", "cwd": "/repo/a",
        "task_id": done_id, "task_title": "finished work",
        "task_status": "done"}
    assert by_trace["sdk-live-chat"] == {
        "trace_id": "sdk-live-chat", "phase": "working", "cwd": "/repo/b",
        "task_id": None, "task_title": None, "task_status": None}


def test_a_reserved_launch_holds_a_starting_slot(flask_client, pool_of_two):
    assert registry.reserve_run("sdk-reserved")
    try:
        body = flask_client.get("/api/agent-runs/slots").get_json()
    finally:
        registry.release_run("sdk-reserved")

    assert body["used"] == 1
    assert body["holders"] == [{
        "trace_id": "sdk-reserved", "phase": "starting", "cwd": None,
        "task_id": None, "task_title": None, "task_status": None}]


def test_refused_run_returns_the_slot_holders(flask_client, live_runs,
                                              pool_of_two):
    live_runs("sdk-one", _runner("/repo/a"))
    live_runs("sdk-two", _runner("/repo/b", working=True))
    task_id = flask_client.post("/api/tasks", json={"title": "next"}) \
        .get_json()["task"]["id"]
    assert flask_client.post(f"/api/tasks/{task_id}/arm",
                             json={}).status_code == 200

    resp = flask_client.post(f"/api/tasks/{task_id}/run")

    assert resp.status_code == 409, resp.get_json()
    slots = resp.get_json()["slots"]
    assert slots["used"] == slots["limit"] == len(slots["holders"]) == 2
    assert {h["trace_id"] for h in slots["holders"]} == {"sdk-one", "sdk-two"}


def test_a_refusal_for_another_reason_carries_no_slots(flask_client):
    task_id = flask_client.post("/api/tasks", json={"title": "unarmed"}) \
        .get_json()["task"]["id"]

    resp = flask_client.post(f"/api/tasks/{task_id}/run")

    assert resp.status_code == 409
    assert "slots" not in resp.get_json()
