"""Feature #81 — a dependency edge carries a kind.

`requires` is the edge that was always there: B consumes A, so A failing makes
B meaningless. `after` is the edge an operator draws only to serialize a pile
of unrelated work, so A failing says nothing about B and must not park it.

The two differ in exactly one place — whether a `failed` dependency counts as
satisfied — and every assertion below is a consequence of that row.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlmodel import select

from lib.agent_sdk import supervisor
from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, AgentTaskDep, TASK_STATUS_ARMED, TASK_STATUS_DONE,
    TASK_STATUS_FAILED, TASK_STATUS_RUNNING,
)
from lib.tasks import deps, runs
from lib.tasks.capture import CaptureRejected


# ── helpers ──────────────────────────────────────────────────

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


def _capture(client, title="a task"):
    resp = client.post("/api/tasks", json={"title": title})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["task"]["id"]


def _armed(client, title="a task", **params):
    task_id = _capture(client, title)
    assert client.post(f"/api/tasks/{task_id}/arm",
                       json=params).status_code == 200
    return task_id


def _task(task_id) -> AgentTask:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


def _stored_kinds(task_id) -> dict[int, str]:
    with SessionLocal() as session:
        rows = session.exec(select(AgentTaskDep)
                            .where(AgentTaskDep.task_id == task_id)).all()
    return {row.depends_on_id: row.kind for row in rows}


def _chain_card(blocker_id: int):
    from lib.agent_messages import store as messages
    return messages.live_keyed_message(runs.TASK_READY_TRACE,
                                       f"chain-stopped:{blocker_id}")


# ── 1. the wire, parsed ──────────────────────────────────────

def test_bare_id_is_a_requires_edge():
    assert deps.parse_deps({"depends_on": [7, 9]}) == [
        deps.DepEdge(7, deps.DEP_KIND_REQUIRES),
        deps.DepEdge(9, deps.DEP_KIND_REQUIRES),
    ]


def test_object_form_stores_its_kind():
    parsed = deps.parse_deps(
        {"depends_on": [{"id": 7, "kind": "after"}, 9]})
    assert parsed == [deps.DepEdge(7, "after"),
                      deps.DepEdge(9, "requires")]


def test_unknown_kind_is_refused():
    with pytest.raises(CaptureRejected) as exc:
        deps.parse_deps({"depends_on": [{"id": 7, "kind": "maybe"}]})
    assert exc.value.field == "depends_on"


def test_one_pair_with_two_kinds_is_refused_by_pair(tmp_db, native_task):
    task_id = native_task(title="dependent")
    dep = native_task(title="dependency")
    parsed = deps.parse_deps(
        {"depends_on": [{"id": dep, "kind": "after"},
                        {"id": dep, "kind": "requires"}]})
    with pytest.raises(deps.DepRejected) as exc:
        deps.set_deps(task_id, parsed)
    assert exc.value.pair == (task_id, dep)


# ── 2. the store ─────────────────────────────────────────────

def test_set_deps_replaces_the_kind_too(tmp_db, native_task):
    task_id = native_task(title="dependent")
    dep = native_task(title="dependency")

    deps.set_deps(task_id, [deps.DepEdge(dep, "after")])
    assert _stored_kinds(task_id) == {dep: "after"}

    deps.set_deps(task_id, [dep])
    assert _stored_kinds(task_id) == {dep: "requires"}


def test_cycle_through_an_after_edge_is_still_refused(tmp_db, native_task):
    a = native_task(title="a")
    b = native_task(title="b")
    deps.set_deps(b, [deps.DepEdge(a, "after")])
    with pytest.raises(deps.DepRejected):
        deps.set_deps(a, [deps.DepEdge(b, "after")])


# ── 3. the rule ──────────────────────────────────────────────

def test_blocking_keeps_a_failed_requires_dependency(tmp_db, native_task):
    task_id = native_task(title="dependent", status=TASK_STATUS_ARMED)
    dep = native_task(title="dependency", status=TASK_STATUS_FAILED)
    deps.set_deps(task_id, [deps.DepEdge(dep, "requires")])
    assert deps.blocking(task_id) == [dep]


def test_blocking_drops_a_failed_after_dependency(tmp_db, native_task):
    task_id = native_task(title="dependent", status=TASK_STATUS_ARMED)
    dep = native_task(title="dependency", status=TASK_STATUS_FAILED)
    deps.set_deps(task_id, [deps.DepEdge(dep, "after")])
    assert deps.blocking(task_id) == []


def test_blocking_keeps_an_unfinished_after_dependency(tmp_db, native_task):
    task_id = native_task(title="dependent", status=TASK_STATUS_ARMED)
    dep = native_task(title="dependency", status=TASK_STATUS_RUNNING)
    deps.set_deps(task_id, [deps.DepEdge(dep, "after")])
    assert deps.blocking(task_id) == [dep]


def test_a_done_dependency_satisfies_either_kind(tmp_db, native_task):
    task_id = native_task(title="dependent", status=TASK_STATUS_ARMED)
    strict = native_task(title="strict", status=TASK_STATUS_DONE)
    loose = native_task(title="loose", status=TASK_STATUS_DONE)
    deps.set_deps(task_id, [deps.DepEdge(strict, "requires"),
                            deps.DepEdge(loose, "after")])
    assert deps.blocking(task_id) == []


def test_failed_blockers_ignores_after_edges(tmp_db, native_task):
    task_id = native_task(title="dependent", status=TASK_STATUS_ARMED)
    strict = native_task(title="strict", status=TASK_STATUS_FAILED)
    loose = native_task(title="loose", status=TASK_STATUS_FAILED)
    deps.set_deps(task_id, [deps.DepEdge(strict, "requires"),
                            deps.DepEdge(loose, "after")])
    assert [row["id"] for row in deps.failed_blockers(task_id)] == [strict]


def test_graph_edges_carry_their_kind(tmp_db, native_task):
    task_id = native_task(title="dependent", status=TASK_STATUS_ARMED)
    dep = native_task(title="dependency", status=TASK_STATUS_FAILED)
    deps.set_deps(task_id, [deps.DepEdge(dep, "after")])

    payload = deps.graph()
    assert payload["edges"] == [{"task_id": task_id, "depends_on_id": dep,
                                 "kind": "after"}]
    node = next(n for n in payload["nodes"] if n["id"] == task_id)
    assert (node["blocked"], node["blocked_by"]) == (False, [])
    assert node["runnable"] is True


# ── 4. the HTTP surface ──────────────────────────────────────

def test_api_put_deps_round_trips_kinds(flask_client):
    strict = _capture(flask_client, "strict")
    loose = _capture(flask_client, "loose")
    task_id = _capture(flask_client, "dependent")

    resp = flask_client.put(
        f"/api/tasks/{task_id}/deps",
        json={"depends_on": [strict, {"id": loose, "kind": "after"}]})

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["depends_on"] == [strict, loose]
    assert body["deps"] == [{"id": strict, "kind": "requires"},
                            {"id": loose, "kind": "after"}]

    edges = flask_client.get("/api/tasks/graph").get_json()["edges"]
    assert {e["depends_on_id"]: e["kind"] for e in edges} == {
        strict: "requires", loose: "after"}


def test_api_put_deps_rejects_an_unknown_kind(flask_client):
    dep = _capture(flask_client, "dependency")
    task_id = _capture(flask_client, "dependent")

    resp = flask_client.put(f"/api/tasks/{task_id}/deps",
                            json={"depends_on": [{"id": dep, "kind": "x"}]})

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "depends_on"


def test_api_put_deps_rejects_a_pair_sent_with_two_kinds(flask_client):
    dep = _capture(flask_client, "dependency")
    task_id = _capture(flask_client, "dependent")

    resp = flask_client.put(
        f"/api/tasks/{task_id}/deps",
        json={"depends_on": [{"id": dep, "kind": "after"}, dep]})

    assert resp.status_code == 400
    assert resp.get_json()["pair"] == [task_id, dep]


# ── 5. what a failure releases ───────────────────────────────

def test_failed_after_dependency_fires_its_dependent(flask_client, launcher):
    dep = _armed(flask_client, "unrelated")
    task_id = _armed(flask_client, "next in the queue")
    flask_client.put(f"/api/tasks/{task_id}/deps",
                     json={"depends_on": [{"id": dep, "kind": "after"}]})
    flask_client.post(f"/api/tasks/{dep}/run")

    launcher.handles[0].finish(status="failed", detail="boom")

    assert _task(task_id).status == "running"
    assert len(launcher.calls) == 2


def test_failed_after_dependency_raises_no_chain_stopped_card(flask_client,
                                                              launcher,
                                                              monkeypatch):
    """Even when the released task cannot start, an `after` edge is not a
    broken chain — nothing was depending on the failed task's result."""
    dep = _armed(flask_client, "unrelated")
    task_id = _armed(flask_client, "next in the queue")
    flask_client.put(f"/api/tasks/{task_id}/deps",
                     json={"depends_on": [{"id": dep, "kind": "after"}]})
    flask_client.post(f"/api/tasks/{dep}/run")

    def _refuse(prompt, **kwargs):
        raise supervisor.LaunchRefused("max_concurrent_runs reached")

    monkeypatch.setattr(runs.supervisor, "launch_run", _refuse)
    launcher.handles[0].finish(status="failed", detail="boom")

    assert _chain_card(dep) is None


def test_failed_requires_dependency_still_parks_its_dependent(flask_client,
                                                              launcher):
    dep = _armed(flask_client, "dependency")
    task_id = _armed(flask_client, "dependent")
    flask_client.put(f"/api/tasks/{task_id}/deps", json={"depends_on": [dep]})
    flask_client.post(f"/api/tasks/{dep}/run")

    launcher.handles[0].finish(status="failed", detail="boom")

    assert _task(task_id).status == "armed"
    assert len(launcher.calls) == 1
    assert _chain_card(dep) is not None
