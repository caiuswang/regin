"""A top-level task's own runs are the sessions of the feature it is (regin:76).

Capture, arm, Run is the path everybody takes, and before this it led out of
the feature population: the task held runs, so it was "a run and not a
feature", could never be shipped, and its sessions were attributed to nothing.
Now the Run click is the declaration. A top-level task that holds runs and
links no other tracker is a feature under its own key, its attempts are its
sessions, it can be shipped, and a run starting over the claim walks it back.

The two-level grain is untouched: a task with children still counts its
children's runs, and a task holding runs still cannot take children.

`launch_run` is stubbed as `test_task_execution.py` stubs it; nothing here
starts an agent.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from sqlmodel import select

from lib.agent_sdk import supervisor
from lib.features import attribution, index
from lib.orm import SessionLocal
from lib.orm.models.agent_runs import AgentRun
from lib.orm.models.tasks import AgentTask, AgentTaskRun
from lib.tasks import runs, store
from lib.tasks.integrations.regin import key_for_task
from tests.feature_ledger_fixtures import task_lane  # noqa: F401


class FakeHandle:
    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self.callback = None

    def add_done_callback(self, callback) -> None:
        self.callback = callback

    def finish(self, status: str = "exited", detail: str = "completed") -> None:
        self.callback(supervisor.RunOutcome(self.trace_id, status, detail))


@pytest.fixture
def launcher(monkeypatch):
    state = SimpleNamespace(handles=[], calls=[])

    def _fake(prompt, **kwargs):
        state.calls.append({"prompt": prompt, **kwargs})
        handle = FakeHandle(f"sdk-fake{len(state.handles)}")
        state.handles.append(handle)
        return handle

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return state


def _capture(client, title="a feature", **body):
    resp = client.post("/api/tasks", json={"title": title, **body})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["task"]["id"]


def _arm(client, task_id, **params):
    resp = client.post(f"/api/tasks/{task_id}/arm", json=params)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["task"]


def _run(client, task_id):
    return client.post(f"/api/tasks/{task_id}/run")


def _runs_of(task_id) -> list[AgentTaskRun]:
    with SessionLocal() as session:
        return list(session.exec(
            select(AgentTaskRun).where(AgentTaskRun.task_id == task_id)))


def _back_run(trace_id: str) -> None:
    """Give a trace id the `agent_runs` row liveness is probed through."""
    with SessionLocal() as session:
        session.add(AgentRun(trace_id=trace_id, status="running",
                             pid=os.getpid()))
        session.commit()


def _task(task_id) -> AgentTask:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


def _index_keys() -> list[str]:
    return [row.key for row in index.page().items]


def _lane_sessions(key: str) -> list[tuple]:
    return [(s.trace_id, s.provenance, s.task_id)
            for s in attribution.sessions_for_feature(key)]


# ── the click puts the task in the population ────────────────

def test_running_a_root_task_puts_it_in_the_feature_population(
        flask_client, launcher):
    task = _capture(flask_client, "the feature")
    _arm(flask_client, task)
    key = key_for_task(task)
    assert key not in _index_keys()

    _run(flask_client, task)

    assert key in _index_keys()
    assert _lane_sessions(key) == [("sdk-fake0", "task-link", task)]


def test_a_second_attempt_is_a_second_session_of_the_same_feature(
        flask_client, launcher):
    task = _capture(flask_client, "the feature")
    _arm(flask_client, task)
    _run(flask_client, task)
    launcher.handles[0].finish()

    resp = _run(flask_client, task)

    assert resp.status_code == 201, resp.get_json()
    assert [s[0] for s in _lane_sessions(key_for_task(task))] == [
        "sdk-fake0", "sdk-fake1"]


def test_a_linked_root_task_counts_under_the_issue_not_its_own_key(
        flask_client, task_lane):
    task_lane(41, ["ENG-41"], [("sdk-linked", "done")])

    assert _lane_sessions("ENG-41") == [("sdk-linked", "task-link", 41)]
    assert _lane_sessions(key_for_task(41)) == []
    assert key_for_task(41) not in _index_keys()


def test_a_run_of_a_feature_counts_under_the_parent_not_itself(
        flask_client, native_task, task_run_row):
    parent = native_task(title="the feature")
    child = native_task(parent_id=parent)
    task_run_row(child, trace_id="sdk-child")

    assert _lane_sessions(key_for_task(parent)) == [
        ("sdk-child", "task-link", child)]
    assert _lane_sessions(key_for_task(child)) == []
    assert key_for_task(child) not in _index_keys()


def test_a_root_task_that_never_ran_is_not_in_the_population(
        flask_client, native_task):
    task = native_task(title="an idea")

    assert key_for_task(task) not in _index_keys()


# ── the prompt names the feature ─────────────────────────────

def test_the_prompt_names_the_tasks_own_feature_key(flask_client, launcher):
    task = _capture(flask_client, "the feature", body="build it")
    _arm(flask_client, task)

    _run(flask_client, task)

    prompt = launcher.calls[0]["prompt"]
    key = key_for_task(task)
    assert "<feature>" in prompt
    assert f"--feature {key}" in prompt
    assert prompt.index("<feature>") < prompt.index("<finish>")


def test_a_run_by_hand_under_a_feature_names_the_parents_key(
        flask_client, launcher):
    feature = _capture(flask_client, "the feature", body="build it")
    child = _capture(flask_client, "a hand-made run", parent_id=feature)
    _arm(flask_client, child)

    _run(flask_client, child)

    prompt = launcher.calls[0]["prompt"]
    assert f"--feature {key_for_task(feature)}" in prompt
    assert key_for_task(child) not in prompt


def test_a_linked_task_gets_the_tracker_block_not_a_feature_block(
        flask_client, launcher, monkeypatch):
    task = _capture(flask_client, "the feature", body="build it")
    _arm(flask_client, task)
    monkeypatch.setattr(runs.links, "list_links", lambda task_id: [
        {"provider": "linear", "external_key": "ENG-7", "url": "", "issue": {}}])

    _run(flask_client, task)

    prompt = launcher.calls[0]["prompt"]
    assert "<tracker>" in prompt
    assert "<feature>" not in prompt


# ── shipping, and running again ──────────────────────────────

def test_a_root_task_that_holds_runs_can_be_shipped(flask_client, launcher):
    task = _capture(flask_client, "the feature")
    _arm(flask_client, task)
    _run(flask_client, task)
    launcher.handles[0].finish()
    assert _task(task).status == "done"
    assert _task(task).claimed_at is None

    view = store.ship_task(task)

    assert view["status"] == "done"
    assert _task(task).claimed_at


def test_running_a_shipped_task_walks_the_claim_back(flask_client, launcher):
    task = _capture(flask_client, "the feature")
    _arm(flask_client, task)
    _run(flask_client, task)
    launcher.handles[0].finish()
    store.ship_task(task)
    assert store.get_task(task)["rounds"] == 1

    resp = _run(flask_client, task)

    assert resp.status_code == 201, resp.get_json()
    row = _task(task)
    assert row.claimed_at is None
    assert row.completions_walked_back == 1
    assert store.get_task(task)["rounds"] == 2


def test_a_settled_root_task_runs_again_without_re_arming(
        flask_client, launcher):
    task = _capture(flask_client, "the feature")
    _arm(flask_client, task, cwd="/tmp/repo")
    _run(flask_client, task)
    launcher.handles[0].finish(status="failed", detail="boom")
    assert _task(task).status == "failed"

    resp = _run(flask_client, task)

    assert resp.status_code == 201, resp.get_json()
    assert launcher.calls[1]["cwd"] == "/tmp/repo"
    assert len(_runs_of(task)) == 2


def test_a_second_run_is_refused_while_the_first_is_live(
        flask_client, launcher):
    task = _capture(flask_client, "the feature")
    _arm(flask_client, task)
    _run(flask_client, task)
    _back_run("sdk-fake0")

    resp = _run(flask_client, task)

    assert resp.status_code == 409
    assert "live run" in resp.get_json()["error"]


def test_a_never_armed_task_is_still_refused(flask_client, launcher):
    task = _capture(flask_client, "the feature")

    resp = _run(flask_client, task)

    assert resp.status_code == 409
    assert "arm it first" in resp.get_json()["error"]


# ── the list ─────────────────────────────────────────────────

def test_a_page_carries_run_and_child_counts_without_a_query_per_row(
        flask_client, native_task, task_run_row, counted_queries):
    parent = native_task(title="a feature with runs")
    for _ in range(3):
        native_task(parent_id=parent)
    attempted = native_task(title="a feature that ran")
    task_run_row(attempted, trace_id="t-1")
    task_run_row(attempted, trace_id="t-2", attempt=2)
    for _ in range(5):
        native_task(title="an idea")

    with counted_queries() as count:
        payload = flask_client.get("/api/tasks?root_only=1&size=50").get_json()

    by_id = {row["id"]: row for row in payload["items"]}
    assert (by_id[parent]["child_count"], by_id[parent]["run_count"]) == (3, 0)
    assert (by_id[attempted]["child_count"],
            by_id[attempted]["run_count"]) == (0, 2)
    assert all((row["child_count"], row["run_count"]) == (0, 0)
               for row in payload["items"]
               if row["id"] not in (parent, attempted))
    assert count.value <= 9


def test_counts_honour_root_only(flask_client, native_task):
    parent = native_task(title="the feature")
    native_task(parent_id=parent)
    native_task(parent_id=parent)

    narrowed = flask_client.get("/api/tasks/counts?root_only=1").get_json()
    everything = flask_client.get("/api/tasks/counts").get_json()

    assert narrowed["total"] == 1
    assert everything["total"] == 3


def test_the_default_task_feed_is_still_unchanged(flask_client, native_task):
    parent = native_task(title="the feature")
    native_task(parent_id=parent)

    payload = flask_client.get("/api/tasks").get_json()

    assert len(payload["items"]) == 2
