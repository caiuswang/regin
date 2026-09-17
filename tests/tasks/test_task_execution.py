"""Slice 3 — arm, run, stop, and the DAG.

Nothing here launches an agent: `supervisor.launch_run` is stubbed, exactly as
`tests/topics/test_proposal_sdk_runner.py` stubs it, and the run's completion
callback is fired by hand — which is also how the idempotence of the terminal
write is pinned.

The liveness rules are the point of several of these: a `running` row is a
claim about intent, so a reader merges it with the pid behind `agent_runs`
rather than trusting it, and a stop never settles the row itself.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from lib.agent_sdk import supervisor
from lib.auth import create_token
from lib.orm import SessionLocal
from lib.orm.models.agent_runs import AgentRun
from lib.orm.models.tasks import AgentTask, AgentTaskDep, AgentTaskRun
from lib.tasks import runs

VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}

# Asserted verbatim on both refusal paths — the liveness check and the
# unique-constraint loser — because a caller must not be able to tell them
# apart.
_LIVE_REFUSAL = "task {task_id} already has a live run"


class FakeHandle:
    """`RunHandle`'s surface, minus the agent."""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self.callback = None

    def add_done_callback(self, callback) -> None:
        self.callback = callback

    def finish(self, status: str = "exited", detail: str = "completed") -> None:
        self.callback(supervisor.RunOutcome(self.trace_id, status, detail))


def _refusing_launch(prompt, **kwargs):
    """`launch_run` with every slot taken — the refusal a release can hit."""
    raise supervisor.LaunchRefused("max_concurrent_runs reached")


@pytest.fixture
def launcher(monkeypatch):
    """A stubbed `launch_run`, recording every call and handing back a handle."""
    state = SimpleNamespace(handles=[], calls=[])

    def _fake(prompt, **kwargs):
        state.calls.append({"prompt": prompt, **kwargs})
        handle = FakeHandle(f"sdk-fake{len(state.handles)}")
        state.handles.append(handle)
        return handle

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return state


def _capture(client, title="a task", **body):
    resp = client.post("/api/tasks", json={"title": title, **body})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["task"]["id"]


def _arm(client, task_id, **params):
    return client.post(f"/api/tasks/{task_id}/arm", json=params)


def _armed(client, title="a task", **params):
    task_id = _capture(client, title)
    assert _arm(client, task_id, **params).status_code == 200
    return task_id


def _task(task_id) -> AgentTask:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


def _run_rows(task_id) -> list[AgentTaskRun]:
    from sqlmodel import select
    with SessionLocal() as session:
        return list(session.exec(
            select(AgentTaskRun).where(AgentTaskRun.task_id == task_id)))


def _back_run(trace_id: str, pid: int, status: str = "running") -> None:
    """Give a trace id an `agent_runs` row — the liveness a reader probes."""
    with SessionLocal() as session:
        session.add(AgentRun(trace_id=trace_id, status=status, pid=pid))
        session.commit()


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# ── 1. arming is the privileged step ─────────────────────────

def test_arm_writes_the_parameters_and_moves_idea_to_armed(flask_client):
    task_id = _capture(flask_client, "wire the thing")

    resp = _arm(flask_client, task_id, cwd="/tmp/repo", model="opus",
                permission_mode="acceptEdits", one_shot=True,
                env={"FOO": "1"}, verify_cmd="pytest -q", autorun=True)

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["task"]["status"] == "armed"
    # The parameters live on the PENDING ATTEMPT, not on the task: an attempt
    # is what spends them, and two attempts may spend different ones.
    armed = body["armed"]
    assert armed["params"]["cwd"] == "/tmp/repo"
    assert armed["params"]["env"] == {"FOO": "1"}
    assert armed["params"]["one_shot"] is True
    assert (armed["verify_cmd"], armed["autorun"]) == ("pytest -q", True)
    assert armed["status"] == "armed"
    assert _task(task_id).status == "armed"


def test_arm_rejects_a_field_outside_the_allowlist_by_name(flask_client):
    task_id = _capture(flask_client)

    resp = _arm(flask_client, task_id, resume="sdk-abc")

    assert resp.status_code == 400
    assert resp.get_json()["field"] == "resume"
    assert "resume" in resp.get_json()["error"]
    assert _task(task_id).status == "idea"
    assert runs.armed_attempt(task_id) is None


def test_arm_checks_the_cli_vocabulary_and_bounds_env(flask_client):
    task_id = _capture(flask_client)

    assert _arm(flask_client, task_id,
                permission_mode="yolo").status_code == 400
    assert _arm(flask_client, task_id, effort="maximum").status_code == 400
    assert _arm(flask_client, task_id,
                env={f"K{n}": "v" for n in range(21)}).status_code == 400
    assert _arm(flask_client, task_id, env={"not an id": "v"}).status_code == 400
    assert _task(task_id).status == "idea"


def test_arm_needs_an_editor_and_a_real_task(flask_client):
    task_id = _capture(flask_client)
    assert flask_client.post(f"/api/tasks/{task_id}/arm", json={},
                             headers=VIEWER).status_code == 403
    assert _arm(flask_client, 9999).status_code == 404


def test_the_execution_routes_are_not_on_the_public_allowlist():
    from web.app import PUBLIC_API_ENDPOINTS
    for endpoint in ("tasks.api_arm_task", "tasks.api_run_task",
                     "tasks.api_stop_task_run", "tasks.api_interrupt_task_run",
                     "tasks.api_task_runs", "tasks.api_set_task_deps",
                     "tasks.api_task_graph"):
        assert endpoint not in PUBLIC_API_ENDPOINTS


# ── 2. capture stays inert ───────────────────────────────────

def test_capture_still_cannot_carry_an_execution_parameter(flask_client,
                                                           capture_enabled,
                                                           mint_key):
    for field in ("autorun", "verify_cmd", "status"):
        resp = flask_client.post("/api/tasks", json={"title": "x", field: 1})
        assert resp.status_code == 400, field
        assert resp.get_json()["field"] == field

    raw, _ = mint_key()
    keyed = flask_client.post(
        "/api/tasks/capture", json={"title": "x", "source": "cli",
                                    "autorun": 1},
        headers={"Authorization": f"Bearer {raw}"})
    assert keyed.status_code == 400
    assert keyed.get_json()["field"] == "autorun"


def test_a_captured_task_lands_inert(flask_client):
    body = flask_client.post("/api/tasks", json={"title": "x"}).get_json()

    assert body["task"]["status"] == "idea"
    # Inert is now structural rather than three NULL columns: an unarmed task
    # has no pending attempt at all.
    assert runs.armed_attempt(body["task"]["id"]) is None


# ── 3-5. running ─────────────────────────────────────────────

def test_run_refuses_with_the_blocking_ids_and_launches_nothing(flask_client,
                                                                launcher):
    first = _armed(flask_client, "first")
    second = _armed(flask_client, "second")
    blocked = _armed(flask_client, "blocked")
    flask_client.put(f"/api/tasks/{blocked}/deps",
                     json={"depends_on": [first, second]})

    resp = flask_client.post(f"/api/tasks/{blocked}/run")

    assert resp.status_code == 409
    assert sorted(resp.get_json()["blocking"]) == sorted([first, second])
    assert str(first) in resp.get_json()["error"]
    assert launcher.calls == []
    assert [r.status for r in _run_rows(blocked)] == ["armed"]
    assert _task(blocked).status == "armed"


def test_the_release_launches_with_the_armed_params_and_records_the_attempt(
        flask_client, launcher):
    dep = _armed(flask_client, "dep")
    task_id = _armed(flask_client, "main", cwd="/tmp/repo", model="opus")
    flask_client.put(f"/api/tasks/{task_id}/deps", json={"depends_on": [dep]})
    flask_client.post(f"/api/tasks/{dep}/run")

    launcher.handles[0].finish()

    rows = _run_rows(task_id)
    assert len(rows) == 1
    assert rows[0].trace_id == launcher.handles[1].trace_id
    assert (rows[0].attempt, rows[0].status) == (1, "running")
    assert launcher.calls[1]["cwd"] == "/tmp/repo"
    assert launcher.calls[1]["model"] == "opus"
    assert "main" in launcher.calls[1]["prompt"]
    assert _task(task_id).status == "running"


def test_a_second_run_while_one_is_live_is_refused(flask_client, launcher):
    task_id = _armed(flask_client)
    flask_client.post(f"/api/tasks/{task_id}/run")
    _back_run(launcher.handles[0].trace_id, os.getpid())

    resp = flask_client.post(f"/api/tasks/{task_id}/run")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == _LIVE_REFUSAL.format(task_id=task_id)
    assert len(launcher.calls) == 1
    assert len(_run_rows(task_id)) == 1


def test_two_racing_runs_leave_one_attempt_and_one_refusal(flask_client,
                                                           launcher,
                                                           monkeypatch):
    """Both racers check liveness before either inserts.

    `UNIQUE(task_id, attempt)` is the arbiter — no lock, no retry — and the
    loser is told the same thing the liveness check tells a later caller, so
    the two paths are indistinguishable from outside.
    """
    task_id = _armed(flask_client)
    monkeypatch.setattr(runs, "live_run", lambda _task_id: None)
    monkeypatch.setattr(runs, "_next_attempt", lambda _session, _task_id: 1)

    first = flask_client.post(f"/api/tasks/{task_id}/run")
    second = flask_client.post(f"/api/tasks/{task_id}/run")

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.get_json()["error"] == _LIVE_REFUSAL.format(task_id=task_id)
    assert [row.attempt for row in _run_rows(task_id)] == [1]
    assert len(launcher.calls) == 1


def test_an_unarmed_task_cannot_run(flask_client, launcher):
    task_id = _capture(flask_client)
    resp = flask_client.post(f"/api/tasks/{task_id}/run")
    assert resp.status_code == 409
    assert "not 'armed'" in resp.get_json()["error"]
    assert launcher.calls == []


def test_run_reports_a_launch_refusal_and_settles_its_own_row(flask_client,
                                                              monkeypatch):
    def _refuse(prompt, **kwargs):
        raise supervisor.LaunchRefused("agent_sdk disabled")

    monkeypatch.setattr(runs.supervisor, "launch_run", _refuse)
    task_id = _armed(flask_client)

    resp = flask_client.post(f"/api/tasks/{task_id}/run")

    assert resp.status_code == 409
    assert "agent_sdk disabled" in resp.get_json()["error"]
    rows = _run_rows(task_id)
    assert (rows[0].status, bool(rows[0].ended_at)) == ("failed", True)
    assert _task(task_id).status == "armed"


def test_run_needs_an_editor_and_a_real_task(flask_client, launcher):
    task_id = _armed(flask_client)
    assert flask_client.post(f"/api/tasks/{task_id}/run",
                             headers=VIEWER).status_code == 403
    assert flask_client.post("/api/tasks/9999/run").status_code == 404


# ── 6. stop delegates, and settles nothing ───────────────────

def test_stop_delegates_to_the_registry_and_leaves_the_row_open(flask_client,
                                                                launcher,
                                                                monkeypatch):
    from lib.agent_sdk import registry

    seen = []
    monkeypatch.setattr(registry, "stop_run",
                        lambda trace_id: (seen.append(trace_id),
                                          (True, "stopping"))[1])
    task_id = _armed(flask_client)
    flask_client.post(f"/api/tasks/{task_id}/run")

    resp = flask_client.post(f"/api/tasks/{task_id}/stop")

    assert resp.status_code == 200
    assert seen == [launcher.handles[0].trace_id]
    assert resp.get_json()["delivered"] is True
    assert resp.get_json()["detail"] == "stopping"

    row = _run_rows(task_id)[0]
    assert (row.status, row.ended_at) == ("running", None)
    assert _task(task_id).status == "running"


def test_interrupt_delegates_to_the_registry(flask_client, launcher,
                                             monkeypatch):
    from lib.agent_sdk import registry

    seen = []
    monkeypatch.setattr(registry, "interrupt_run",
                        lambda trace_id: (seen.append(trace_id),
                                          (True, "interrupt sent"))[1])
    task_id = _armed(flask_client)
    flask_client.post(f"/api/tasks/{task_id}/run")

    resp = flask_client.post(f"/api/tasks/{task_id}/interrupt")

    assert seen == [launcher.handles[0].trace_id]
    assert resp.get_json()["detail"] == "interrupt sent"


def test_stopping_a_task_that_never_ran_is_a_409(flask_client):
    task_id = _armed(flask_client)
    resp = flask_client.post(f"/api/tasks/{task_id}/stop")
    assert resp.status_code == 409
    assert "no run to control" in resp.get_json()["error"]


# ── 7. the terminal write ────────────────────────────────────

def test_completion_settles_the_attempt_exactly_once(flask_client, launcher):
    task_id = _armed(flask_client)
    flask_client.post(f"/api/tasks/{task_id}/run")
    handle = launcher.handles[0]

    handle.finish()
    first = _run_rows(task_id)[0]
    ended_at, status = first.ended_at, first.status

    handle.finish(status="failed", detail="second call")

    row = _run_rows(task_id)[0]
    assert (row.status, row.ended_at) == (status, ended_at)
    assert row.exit_reason == "completed"
    assert (status, _task(task_id).status) == ("done", "done")


def test_a_failed_run_fails_its_task(flask_client, launcher):
    task_id = _armed(flask_client)
    flask_client.post(f"/api/tasks/{task_id}/run")

    launcher.handles[0].finish(status="failed", detail="boom")

    assert _run_rows(task_id)[0].status == "failed"
    assert _task(task_id).status == "failed"


def test_a_second_attempt_increments_and_keeps_the_first(flask_client,
                                                         launcher):
    task_id = _armed(flask_client)
    flask_client.post(f"/api/tasks/{task_id}/run")
    launcher.handles[0].finish()
    _arm(flask_client, task_id)

    flask_client.post(f"/api/tasks/{task_id}/run")

    attempts = sorted(row.attempt for row in _run_rows(task_id))
    assert attempts == [1, 2]


# ── 8. a status row is a claim, not proof of liveness ────────

def test_a_running_row_with_a_dead_process_reads_as_not_live(flask_client,
                                                             launcher):
    task_id = _armed(flask_client)
    flask_client.post(f"/api/tasks/{task_id}/run")
    _back_run(launcher.handles[0].trace_id, _dead_pid())

    payload = flask_client.get(f"/api/tasks/{task_id}/runs").get_json()

    assert payload["runs"][0]["status"] == "running"
    assert payload["runs"][0]["live"] is False
    assert payload["runs"][0]["agent_run"]["status"] == "running"


def test_a_running_row_behind_a_live_process_reads_as_live(flask_client,
                                                           launcher):
    task_id = _armed(flask_client)
    flask_client.post(f"/api/tasks/{task_id}/run")
    _back_run(launcher.handles[0].trace_id, os.getpid())

    payload = flask_client.get(f"/api/tasks/{task_id}/runs").get_json()
    assert payload["runs"][0]["live"] is True


def test_a_dead_run_does_not_strand_its_task(flask_client, launcher):
    """The task's `running` claim outlives the process too, so a stranded task
    is re-runnable once nothing is proven to be behind it."""
    task_id = _armed(flask_client)
    flask_client.post(f"/api/tasks/{task_id}/run")
    _back_run(launcher.handles[0].trace_id, _dead_pid())

    assert flask_client.post(f"/api/tasks/{task_id}/run").status_code == 201
    assert [row.attempt for row in _run_rows(task_id)] == [1, 2]


def test_the_run_history_is_newest_first_and_empty_is_not_an_error(
        flask_client, launcher):
    task_id = _armed(flask_client)
    assert flask_client.get(f"/api/tasks/{task_id}/runs").get_json() == {
        "runs": []}

    flask_client.post(f"/api/tasks/{task_id}/run")
    launcher.handles[0].finish()
    _arm(flask_client, task_id)
    flask_client.post(f"/api/tasks/{task_id}/run")

    payload = flask_client.get(f"/api/tasks/{task_id}/runs").get_json()
    assert [run["attempt"] for run in payload["runs"]] == [2, 1]


# ── 9. the DAG ───────────────────────────────────────────────

def test_a_self_edge_is_refused_by_pair(flask_client):
    task_id = _capture(flask_client)

    resp = flask_client.put(f"/api/tasks/{task_id}/deps",
                            json={"depends_on": [task_id]})

    assert resp.status_code == 400
    assert resp.get_json()["pair"] == [task_id, task_id]
    assert "itself" in resp.get_json()["error"]


def test_a_cycle_is_refused_by_pair_and_the_dag_survives(flask_client):
    first = _capture(flask_client, "first")
    second = _capture(flask_client, "second")
    third = _capture(flask_client, "third")
    flask_client.put(f"/api/tasks/{second}/deps", json={"depends_on": [first]})
    flask_client.put(f"/api/tasks/{third}/deps", json={"depends_on": [second]})

    resp = flask_client.put(f"/api/tasks/{first}/deps",
                            json={"depends_on": [third]})

    assert resp.status_code == 400
    assert resp.get_json()["pair"] == [first, third]
    assert "cycle" in resp.get_json()["error"]
    with SessionLocal() as session:
        from sqlmodel import select
        edges = {(r.task_id, r.depends_on_id)
                 for r in session.exec(select(AgentTaskDep))}
    assert edges == {(second, first), (third, second)}


def test_deps_replace_rather_than_append_and_reject_unknown_ids(flask_client):
    first = _capture(flask_client, "first")
    second = _capture(flask_client, "second")
    task_id = _capture(flask_client, "dependent")

    flask_client.put(f"/api/tasks/{task_id}/deps",
                     json={"depends_on": [first, second]})
    resp = flask_client.put(f"/api/tasks/{task_id}/deps",
                            json={"depends_on": [second]})
    assert resp.get_json()["depends_on"] == [second]

    cleared = flask_client.put(f"/api/tasks/{task_id}/deps",
                               json={"depends_on": []})
    assert cleared.get_json()["depends_on"] == []

    unknown = flask_client.put(f"/api/tasks/{task_id}/deps",
                               json={"depends_on": [9999]})
    assert unknown.status_code == 400
    assert "unknown task 9999" in unknown.get_json()["error"]


def test_deps_needs_an_editor_a_real_task_and_a_list_of_ids(flask_client):
    task_id = _capture(flask_client)
    assert flask_client.put(f"/api/tasks/{task_id}/deps",
                            json={"depends_on": []},
                            headers=VIEWER).status_code == 403
    assert flask_client.put("/api/tasks/9999/deps",
                            json={"depends_on": []}).status_code == 404
    assert flask_client.put(f"/api/tasks/{task_id}/deps",
                            json={"depends_on": ["x"]}).status_code == 400
    assert flask_client.put(f"/api/tasks/{task_id}/deps",
                            json={"after": [1]}).status_code == 400


# ── 10. firing is event-driven ───────────────────────────────

def _chain(flask_client, **dependent_params):
    """`(dependency, dependent)`, both armed, the second waiting on the first."""
    dep = _armed(flask_client, "dependency")
    task_id = _armed(flask_client, "dependent", **dependent_params)
    flask_client.put(f"/api/tasks/{task_id}/deps", json={"depends_on": [dep]})
    return dep, task_id


def test_a_released_dependent_launches_without_a_second_opt_in(flask_client,
                                                               launcher):
    """The edge is the grant: no `autorun` is asked for or needed."""
    dep, task_id = _chain(flask_client)
    flask_client.post(f"/api/tasks/{dep}/run")

    launcher.handles[0].finish()

    assert len(launcher.calls) == 2
    assert _task(task_id).status == "running"
    assert _run_rows(task_id)[0].trace_id == launcher.handles[1].trace_id


def test_a_dependent_with_autorun_on_launches_itself(flask_client, launcher):
    dep, task_id = _chain(flask_client, autorun=True)
    flask_client.post(f"/api/tasks/{dep}/run")

    launcher.handles[0].finish()

    assert len(launcher.calls) == 2
    assert _task(task_id).status == "running"
    assert _run_rows(task_id)[0].trace_id == launcher.handles[1].trace_id


def test_a_release_the_launcher_refuses_leaves_the_task_armed_and_says_so(
        flask_client, launcher, monkeypatch):
    from lib.agent_messages import store as messages

    dep, task_id = _chain(flask_client)
    flask_client.post(f"/api/tasks/{dep}/run")

    monkeypatch.setattr(runs.supervisor, "launch_run", _refusing_launch)
    launcher.handles[0].finish()

    assert _task(task_id).status == "armed"
    card = messages.live_keyed_message(runs.TASK_READY_TRACE,
                                       f"task-ready:{task_id}")
    assert card is not None
    assert "max_concurrent_runs reached" in card["body"]


def test_arming_a_task_whose_dependencies_are_already_done_launches_it(
        flask_client, launcher):
    """The release the DAG would never send: nothing completes after the arm."""
    dep = _armed(flask_client, "dependency")
    task_id = _capture(flask_client, "dependent")
    flask_client.put(f"/api/tasks/{task_id}/deps", json={"depends_on": [dep]})
    flask_client.post(f"/api/tasks/{dep}/run")
    launcher.handles[0].finish()
    assert _task(task_id).status == "idea"

    armed = _arm(flask_client, task_id)

    assert armed.get_json()["task"]["status"] == "running"
    assert _task(task_id).status == "running"
    assert len(launcher.calls) == 2


def test_arming_a_task_still_blocked_does_not_launch_it(flask_client, launcher):
    dep = _armed(flask_client, "dependency")
    task_id = _capture(flask_client, "dependent")
    flask_client.put(f"/api/tasks/{task_id}/deps", json={"depends_on": [dep]})

    armed = _arm(flask_client, task_id)

    assert armed.get_json()["task"]["status"] == "armed"
    assert launcher.calls == []


def test_arming_a_task_with_no_dependencies_never_launches_it(flask_client,
                                                              launcher):
    """Arming writes parameters; with no edge there is nothing to release."""
    task_id = _armed(flask_client, "solo", autorun=True)

    assert launcher.calls == []
    assert _task(task_id).status == "armed"


def test_dropping_the_last_blocking_edge_launches_the_armed_task(flask_client,
                                                                 launcher):
    done = _armed(flask_client, "finished")
    pending = _armed(flask_client, "unfinished")
    task_id = _armed(flask_client, "dependent")
    flask_client.put(f"/api/tasks/{task_id}/deps",
                     json={"depends_on": [done, pending]})
    flask_client.post(f"/api/tasks/{done}/run")
    launcher.handles[0].finish()
    assert _task(task_id).status == "armed"

    flask_client.put(f"/api/tasks/{task_id}/deps", json={"depends_on": [done]})

    assert _task(task_id).status == "running"
    assert len(launcher.calls) == 2


def test_editing_deps_while_one_is_unfinished_leaves_the_task_armed(
        flask_client, launcher):
    done = _armed(flask_client, "finished")
    pending = _armed(flask_client, "unfinished")
    other = _armed(flask_client, "another")
    task_id = _armed(flask_client, "dependent")
    flask_client.put(f"/api/tasks/{task_id}/deps",
                     json={"depends_on": [done, pending]})
    flask_client.post(f"/api/tasks/{done}/run")
    launcher.handles[0].finish()

    flask_client.put(f"/api/tasks/{task_id}/deps",
                     json={"depends_on": [pending, other]})

    assert _task(task_id).status == "armed"
    assert len(launcher.calls) == 1


def test_a_launch_fault_is_reported_as_a_task_that_did_not_start(flask_client,
                                                                 launcher,
                                                                 monkeypatch):
    """The arm already committed: a broken launch must not read as a 500."""
    dep = _armed(flask_client, "dependency")
    task_id = _capture(flask_client, "dependent")
    flask_client.put(f"/api/tasks/{task_id}/deps", json={"depends_on": [dep]})
    flask_client.post(f"/api/tasks/{dep}/run")
    launcher.handles[0].finish()

    broken = SimpleNamespace(on=True)
    real = runs.supervisor.launch_run

    def _maybe(prompt, **kwargs):
        if broken.on:
            raise OSError("git is not on PATH")
        return real(prompt, **kwargs)

    monkeypatch.setattr(runs.supervisor, "launch_run", _maybe)
    armed = _arm(flask_client, task_id)

    assert armed.status_code == 200
    assert armed.get_json()["task"]["status"] == "armed"
    # The attempt the fault left behind minted no trace id, so it cannot read
    # as live — the task the release could not start is still runnable by hand.
    assert runs.live_run(task_id) is None
    broken.on = False
    assert flask_client.post(f"/api/tasks/{task_id}/run").status_code == 201


def test_a_launch_retires_the_card_an_earlier_refusal_left(flask_client,
                                                           launcher,
                                                           monkeypatch):
    """`_announce_stuck` emits `once` — a live card would gag the next reason."""
    from lib.agent_messages import store as messages

    refuse = SimpleNamespace(on=True)
    real = runs.supervisor.launch_run

    def _maybe(prompt, **kwargs):
        if refuse.on:
            return _refusing_launch(prompt, **kwargs)
        return real(prompt, **kwargs)

    dep, task_id = _chain(flask_client)
    flask_client.post(f"/api/tasks/{dep}/run")
    monkeypatch.setattr(runs.supervisor, "launch_run", _maybe)
    launcher.handles[0].finish()
    assert messages.live_keyed_message(runs.TASK_READY_TRACE,
                                       f"task-ready:{task_id}") is not None

    refuse.on = False
    assert flask_client.post(f"/api/tasks/{task_id}/run").status_code == 201

    assert messages.live_keyed_message(runs.TASK_READY_TRACE,
                                       f"task-ready:{task_id}") is None


def test_clearing_every_edge_leaves_the_task_armed(flask_client, launcher):
    """A task with no dependencies is nobody's dependent — nothing releases it."""
    dep = _armed(flask_client, "dependency")
    task_id = _armed(flask_client, "dependent")
    flask_client.put(f"/api/tasks/{task_id}/deps", json={"depends_on": [dep]})

    flask_client.put(f"/api/tasks/{task_id}/deps", json={"depends_on": []})

    assert _task(task_id).status == "armed"
    assert launcher.calls == []


def test_a_dependent_still_blocked_is_left_alone(flask_client, launcher):
    first = _armed(flask_client, "first")
    second = _armed(flask_client, "second")
    task_id = _armed(flask_client, "dependent", autorun=True)
    flask_client.put(f"/api/tasks/{task_id}/deps",
                     json={"depends_on": [first, second]})
    flask_client.post(f"/api/tasks/{first}/run")

    launcher.handles[0].finish()

    assert len(launcher.calls) == 1
    assert _task(task_id).status == "armed"


def test_a_failed_dependency_fires_nothing(flask_client, launcher):
    dep, task_id = _chain(flask_client, autorun=True)
    flask_client.post(f"/api/tasks/{dep}/run")

    launcher.handles[0].finish(status="failed", detail="boom")

    assert len(launcher.calls) == 1
    assert _task(task_id).status == "armed"


def test_firing_twice_launches_the_dependent_once(flask_client, launcher):
    dep, task_id = _chain(flask_client, autorun=True)
    flask_client.post(f"/api/tasks/{dep}/run")

    launcher.handles[0].finish()
    launcher.handles[0].finish()

    assert len(launcher.calls) == 2
    assert len(_run_rows(task_id)) == 1


# ── 11. the graph view ───────────────────────────────────────

def _graph_nodes(flask_client) -> dict:
    payload = flask_client.get("/api/tasks/graph").get_json()
    return {node["id"]: node for node in payload["nodes"]}


def test_the_graph_carries_every_node_edge_and_flag(flask_client):
    dep, task_id = _chain(flask_client)
    idea = _capture(flask_client, "just an idea")

    payload = flask_client.get("/api/tasks/graph").get_json()
    nodes = {node["id"]: node for node in payload["nodes"]}

    assert set(nodes) == {dep, task_id, idea}
    assert payload["edges"] == [{"task_id": task_id, "depends_on_id": dep,
                                 "kind": "requires"}]
    assert (nodes[dep]["blocked"], nodes[dep]["runnable"]) == (False, True)
    assert (nodes[task_id]["blocked"], nodes[task_id]["runnable"]) == (True,
                                                                       False)
    assert nodes[task_id]["blocked_by"] == [dep]
    assert (nodes[idea]["blocked"], nodes[idea]["runnable"]) == (False, False)


def test_a_finished_dependency_makes_its_dependent_running_in_the_graph(
        flask_client, launcher):
    dep, task_id = _chain(flask_client)
    flask_client.post(f"/api/tasks/{dep}/run")

    launcher.handles[0].finish()

    nodes = _graph_nodes(flask_client)
    assert nodes[dep]["status"] == "done"
    assert nodes[task_id]["status"] == "running"
    assert nodes[task_id]["blocked"] is False


def test_a_dependent_the_launcher_refused_reads_runnable_in_the_graph(
        flask_client, launcher, monkeypatch):
    """`runnable` survives as the state of a task that came due and could not
    start — the one an operator has to run by hand."""
    dep, task_id = _chain(flask_client)
    flask_client.post(f"/api/tasks/{dep}/run")

    monkeypatch.setattr(runs.supervisor, "launch_run", _refusing_launch)
    launcher.handles[0].finish()

    nodes = _graph_nodes(flask_client)
    assert (nodes[task_id]["blocked"], nodes[task_id]["runnable"]) == (False,
                                                                       True)


def test_the_graph_is_empty_not_an_error(flask_client):
    payload = flask_client.get("/api/tasks/graph").get_json()
    assert payload["nodes"] == [] and payload["edges"] == []
    assert payload["truncated"] is False and payload["total_nodes"] == 0


def test_the_graph_needs_an_editor(flask_client):
    assert flask_client.get("/api/tasks/graph",
                            headers=VIEWER).status_code == 403


# ── 12. the agent's own finish signal ────────────────────────

def _finish(flask_client, run_id, remote="127.0.0.1", **body):
    return flask_client.post(f"/api/tasks/runs/{run_id}/finish", json=body,
                             environ_base={"REMOTE_ADDR": remote})


def _launched_run_id(flask_client, task_id) -> int:
    resp = flask_client.post(f"/api/tasks/{task_id}/run")
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["run"]["id"]


def test_the_launch_prompt_names_the_exact_finish_command(flask_client,
                                                          launcher):
    task_id = _armed(flask_client, "wire the thing")
    run_id = _launched_run_id(flask_client, task_id)

    prompt = launcher.calls[0]["prompt"]
    assert runs._finish_command(run_id) in prompt
    assert prompt.startswith("wire the thing")
    assert "--status failed" in prompt


def test_the_launch_prompt_leaves_the_title_behind_when_a_body_exists(
        flask_client, launcher):
    task_id = _capture(flask_client, "shelf label", body="wire the thing")
    assert _arm(flask_client, task_id).status_code == 200
    run_id = _launched_run_id(flask_client, task_id)

    prompt = launcher.calls[0]["prompt"]
    assert prompt.split("<finish>")[0].strip() == "wire the thing"
    assert "shelf label" not in prompt


def test_a_maximal_body_cannot_truncate_the_finish_block(flask_client,
                                                         launcher):
    task_id = _capture(flask_client, "big", body="x" * runs.PROMPT_MAX)
    assert _arm(flask_client, task_id).status_code == 200
    run_id = _launched_run_id(flask_client, task_id)

    prompt = launcher.calls[0]["prompt"]
    assert runs._finish_command(run_id) in prompt
    assert len(prompt) > runs.PROMPT_MAX


def test_finish_settles_the_attempt_and_fires_the_dependent(flask_client,
                                                            launcher):
    dep, task_id = _chain(flask_client, autorun=True)
    run_id = _launched_run_id(flask_client, dep)

    body = _finish(flask_client, run_id).get_json()

    assert (body["settled"], body["fired"]) == (True, [task_id])
    assert _task(dep).status == "done"
    assert _run_rows(dep)[0].ended_at is not None
    assert _task(task_id).status == "running"
    assert len(launcher.calls) == 2


def test_finish_leaves_the_session_open_and_stoppable(flask_client, launcher):
    task_id = _armed(flask_client, "a task")
    run_id = _launched_run_id(flask_client, task_id)
    trace_id = launcher.handles[0].trace_id
    _back_run(trace_id, os.getpid())

    _finish(flask_client, run_id)

    # The run regin launched is untouched: only the attempt was settled.
    with SessionLocal() as session:
        from sqlmodel import select
        row = session.exec(
            select(AgentRun).where(AgentRun.trace_id == trace_id)).first()
    assert row.status == "running"

    stopped = []
    import lib.agent_sdk.registry as registry
    registry.stop_run = lambda tid: (stopped.append(tid), (True, "asked"))[1]
    resp = flask_client.post(f"/api/tasks/{task_id}/stop")
    assert resp.status_code == 200 and stopped == [trace_id]


def test_a_second_finish_is_a_no_op_that_fires_nothing(flask_client, launcher):
    dep, task_id = _chain(flask_client, autorun=True)
    run_id = _launched_run_id(flask_client, dep)

    _finish(flask_client, run_id)
    second = _finish(flask_client, run_id).get_json()

    assert (second["settled"], second["fired"]) == (False, [])
    assert len(_run_rows(task_id)) == 1
    assert len(launcher.calls) == 2


def test_the_run_callback_cannot_re_fire_what_finish_already_did(flask_client,
                                                                 launcher):
    dep, task_id = _chain(flask_client, autorun=True)
    run_id = _launched_run_id(flask_client, dep)

    _finish(flask_client, run_id)
    launcher.handles[0].finish()          # the session ends later

    assert len(_run_rows(task_id)) == 1
    assert _task(dep).status == "done"
    assert _run_rows(dep)[0].exit_reason == "agent signalled finish"


def test_a_run_that_never_signals_still_settles_on_the_callback(flask_client,
                                                                launcher):
    dep, task_id = _chain(flask_client, autorun=True)
    _launched_run_id(flask_client, dep)

    launcher.handles[0].finish()

    assert _task(dep).status == "done"
    assert _task(task_id).status == "running"


def test_finish_failed_fails_the_task_and_releases_nothing(flask_client,
                                                           launcher):
    dep, task_id = _chain(flask_client, autorun=True)
    run_id = _launched_run_id(flask_client, dep)

    body = _finish(flask_client, run_id, status="failed",
                   reason="could not build").get_json()

    assert body["fired"] == []
    assert _task(dep).status == "failed"
    assert _run_rows(dep)[0].exit_reason == "could not build"
    assert _task(task_id).status == "armed"
    assert len(launcher.calls) == 1


def test_a_dependent_is_launched_by_a_finish(flask_client, launcher):
    dep, task_id = _chain(flask_client)
    run_id = _launched_run_id(flask_client, dep)

    assert _finish(flask_client, run_id).get_json()["fired"] == [task_id]

    assert _task(task_id).status == "running"


def test_finish_rejects_an_unknown_run_status_or_field(flask_client, launcher):
    task_id = _armed(flask_client, "a task")
    run_id = _launched_run_id(flask_client, task_id)

    bad_status = _finish(flask_client, run_id, status="cancelled")
    assert bad_status.status_code == 400
    assert bad_status.get_json()["field"] == "status"

    bad_field = _finish(flask_client, run_id, verify_exit_code=0)
    assert bad_field.status_code == 400
    assert bad_field.get_json()["field"] == "verify_exit_code"

    assert _run_rows(task_id)[0].ended_at is None
    assert _finish(flask_client, 9999).status_code == 404


def test_finish_is_loopback_only_and_off_the_public_allowlist(flask_client,
                                                              anon_client,
                                                              launcher):
    """The agent holds no JWT, so loopback IS the guard — proven with a client
    that carries no token at all, from both sides of the boundary."""
    from web.app import PUBLIC_API_ENDPOINTS
    assert "tasks.api_finish_task_run" not in PUBLIC_API_ENDPOINTS

    task_id = _armed(flask_client, "a task")
    run_id = _launched_run_id(flask_client, task_id)

    remote = _finish(anon_client, run_id, remote="10.0.0.5")
    assert remote.status_code == 401
    assert _run_rows(task_id)[0].ended_at is None

    assert _finish(anon_client, run_id).status_code == 200
    assert _run_rows(task_id)[0].ended_at is not None


# ── 13. the CLI the prompt actually names ────────────────────

def _cli_against(flask_client, monkeypatch):
    """Point `urllib` at the Flask test client, so `regin task finish` runs for
    real — its settings lookup, its URL, its body — against this DB.

    The route is exercised elsewhere; what this covers is the half no HTTP test
    can reach, which is the half the agent actually executes.
    """
    import urllib.request
    from urllib.error import HTTPError

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake(request, timeout=None):
        resp = flask_client.post(request.full_url, data=request.data,
                                 content_type="application/json",
                                 environ_base={"REMOTE_ADDR": "127.0.0.1"})
        if resp.status_code >= 400:
            raise HTTPError(request.full_url, resp.status_code, "refused",
                            None, io.BytesIO(resp.data))
        return _Response(resp.data)

    monkeypatch.setattr(urllib.request, "urlopen", _fake)


def test_the_cli_the_prompt_names_settles_the_run_and_fires(flask_client,
                                                            launcher,
                                                            monkeypatch):
    from typer.testing import CliRunner
    from cli.commands.tasks import tasks_app

    dep, task_id = _chain(flask_client, autorun=True)
    run_id = _launched_run_id(flask_client, dep)
    _cli_against(flask_client, monkeypatch)

    result = CliRunner().invoke(tasks_app, ["finish", str(run_id)])

    assert result.exit_code == 0, result.output
    assert _task(dep).status == "done"
    assert _task(task_id).status == "running"
    assert f"#{task_id}" in result.output


def test_the_cli_exits_nonzero_so_the_agent_can_retry(flask_client, launcher,
                                                      monkeypatch):
    from typer.testing import CliRunner
    from cli.commands.tasks import tasks_app

    _cli_against(flask_client, monkeypatch)

    result = CliRunner().invoke(tasks_app, ["finish", "9999"])

    assert result.exit_code == 1


def test_the_cli_reports_a_failure_reason_onto_the_attempt(flask_client,
                                                           launcher,
                                                           monkeypatch):
    from typer.testing import CliRunner
    from cli.commands.tasks import tasks_app

    dep, task_id = _chain(flask_client, autorun=True)
    run_id = _launched_run_id(flask_client, dep)
    _cli_against(flask_client, monkeypatch)

    result = CliRunner().invoke(
        tasks_app, ["finish", str(run_id), "--status", "failed",
                    "--reason", "the build never went green"])

    assert result.exit_code == 0, result.output
    assert _run_rows(dep)[0].exit_reason == "the build never went green"
    assert _task(task_id).status == "armed"
    assert len(launcher.calls) == 1


def test_a_finished_attempt_whose_agent_lives_still_refuses_a_second_run(
        flask_client, launcher):
    """`finish` settles the attempt but deliberately leaves the session up, so
    `ended_at` alone must not re-open the door: two agents in one worktree, and
    Stop could only ever reach the newer."""
    task_id = _armed(flask_client, "a task")
    run_id = _launched_run_id(flask_client, task_id)
    _back_run(launcher.handles[0].trace_id, os.getpid())

    _finish(flask_client, run_id)
    assert _arm(flask_client, task_id).status_code == 200

    resp = flask_client.post(f"/api/tasks/{task_id}/run")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == _LIVE_REFUSAL.format(task_id=task_id)
    assert len(launcher.calls) == 1


def test_a_finished_attempt_whose_agent_died_lets_the_task_run_again(
        flask_client, launcher):
    task_id = _armed(flask_client, "a task")
    run_id = _launched_run_id(flask_client, task_id)
    _back_run(launcher.handles[0].trace_id, _dead_pid(), status="exited")

    _finish(flask_client, run_id)
    assert _arm(flask_client, task_id).status_code == 200

    assert flask_client.post(f"/api/tasks/{task_id}/run").status_code == 201
    assert len(launcher.calls) == 2


def test_the_run_history_agrees_with_the_refusal_after_a_finish(flask_client,
                                                                launcher):
    """A 409 naming a live run, over a history that calls every attempt dead,
    is a contradiction an operator cannot act on."""
    task_id = _armed(flask_client, "a task")
    run_id = _launched_run_id(flask_client, task_id)
    _back_run(launcher.handles[0].trace_id, os.getpid())
    _finish(flask_client, run_id)
    assert _arm(flask_client, task_id).status_code == 200

    refusal = flask_client.post(f"/api/tasks/{task_id}/run")
    history = flask_client.get(f"/api/tasks/{task_id}/runs").get_json()["runs"]

    assert refusal.status_code == 409
    assert history[0]["live"] is True
    assert history[0]["ended_at"] is not None


# ── 9. an armed skill opens the run ──────────────────────────

def test_arm_stores_a_skill_and_strips_its_leading_slash(flask_client):
    task_id = _capture(flask_client, "review it")
    resp = _arm(flask_client, task_id,
                skill={"name": "/goal-verified", "args": "--strict"})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["armed"]["params"]["skill"] == {
        "name": "goal-verified", "args": "--strict"}


def test_arm_rejects_a_skill_that_is_not_a_slash_command(flask_client):
    task_id = _capture(flask_client, "review it")
    for bad in ({"name": "has space"}, {"name": "-leading"}, "goal-verified"):
        resp = _arm(flask_client, task_id, skill=bad)
        assert resp.status_code == 400, bad
        assert resp.get_json()["field"] == "skill"
    assert _task(task_id).status == "idea"


def test_arm_with_an_empty_skill_name_means_no_skill(flask_client):
    task_id = _capture(flask_client, "review it")
    resp = _arm(flask_client, task_id, skill={"name": "  "})
    assert resp.status_code == 200
    assert resp.get_json()["armed"]["params"]["skill"] is None


def test_the_launch_prompt_opens_with_the_skill_invocation(flask_client,
                                                            launcher):
    task_id = _capture(flask_client, "shelf", body="wire the thing")
    assert _arm(flask_client, task_id,
                skill={"name": "goal-verified", "args": "fast"}).status_code == 200
    run_id = _launched_run_id(flask_client, task_id)

    prompt = launcher.calls[0]["prompt"]
    assert prompt.startswith("/goal-verified fast\n\nwire the thing")
    assert runs._finish_command(run_id) in prompt


def test_a_skill_without_args_takes_the_body_as_its_subject(flask_client,
                                                            launcher):
    task_id = _capture(flask_client, "shelf", body="wire the thing")
    assert _arm(flask_client, task_id,
                skill={"name": "tdd"}).status_code == 200
    _launched_run_id(flask_client, task_id)

    prompt = launcher.calls[0]["prompt"]
    assert prompt.startswith("/tdd\n\nwire the thing")


def test_re_arming_without_a_skill_drops_the_invocation(flask_client,
                                                        launcher):
    task_id = _capture(flask_client, "shelf", body="wire the thing")
    assert _arm(flask_client, task_id,
                skill={"name": "tdd"}).status_code == 200
    assert _arm(flask_client, task_id).status_code == 200
    _launched_run_id(flask_client, task_id)

    assert launcher.calls[0]["prompt"].startswith("wire the thing")


# ── R5: the broken chain speaks ───────────────────────────────

def _chain_card(blocker_id: int):
    from lib.agent_messages import store as messages
    return messages.live_keyed_message(runs.TASK_READY_TRACE,
                                       f"chain-stopped:{blocker_id}")


def test_a_failed_slice_announces_the_broken_chain(flask_client, launcher):
    """A `failed` dependency parks its dependents looking like a normal wait —
    the card is what tells the two apart, and it names the slice that broke."""
    dep, task_id = _chain(flask_client)
    flask_client.post(f"/api/tasks/{dep}/run")

    launcher.handles[0].finish(status="failed", detail="DO-NOT-SHIP")

    assert _task(dep).status == "failed"
    assert _task(task_id).status == "armed"
    card = _chain_card(dep)
    assert card is not None
    assert str(dep) in card["body"]
    assert str(task_id) in card["body"]


def test_a_merely_unfinished_dependency_announces_nothing(flask_client,
                                                          launcher):
    """Waiting on a slice that has not settled is the normal case, not a break:
    one of the two dependencies landed, the other is still running."""
    running = _armed(flask_client, "still running")
    landing = _armed(flask_client, "lands now")
    task_id = _armed(flask_client, "dependent")
    flask_client.put(f"/api/tasks/{task_id}/deps",
                     json={"depends_on": [running, landing]})
    flask_client.post(f"/api/tasks/{running}/run")
    flask_client.post(f"/api/tasks/{landing}/run")

    launcher.handles[1].finish()

    assert _task(running).status == "running"
    assert _task(task_id).status == "armed"
    assert _chain_card(running) is None
    assert _chain_card(landing) is None


def test_the_named_slice_is_the_failed_one_not_the_completing_one(flask_client,
                                                                  launcher):
    """Two dependencies, the older one failed: the card names *it*, because
    that is the row a human has to re-arm."""
    broke = _armed(flask_client, "broke earlier")
    lands = _armed(flask_client, "lands now")
    task_id = _armed(flask_client, "dependent")
    flask_client.put(f"/api/tasks/{task_id}/deps",
                     json={"depends_on": [broke, lands]})
    flask_client.post(f"/api/tasks/{broke}/run")
    launcher.handles[0].finish(status="failed")
    flask_client.post(f"/api/tasks/{lands}/run")

    launcher.handles[1].finish()

    assert _chain_card(lands) is None
    assert str(task_id) in _chain_card(broke)["body"]


def test_running_the_failed_slice_again_retires_its_chain_card(flask_client,
                                                               launcher):
    """`once` gags the next card, so the slice starting again must clear it —
    otherwise a second break of the same slice is announced to nobody."""
    dep, task_id = _chain(flask_client)
    flask_client.post(f"/api/tasks/{dep}/run")
    launcher.handles[0].finish(status="failed")
    assert _chain_card(dep) is not None

    assert flask_client.post(f"/api/tasks/{dep}/run").status_code == 201

    assert _chain_card(dep) is None


def test_a_dependent_that_launched_is_not_announced(flask_client, launcher):
    """The dependent fired, so nothing is holding it — a card here would be a
    false alarm on the healthy path."""
    dep, task_id = _chain(flask_client)
    flask_client.post(f"/api/tasks/{dep}/run")

    launcher.handles[0].finish()

    assert _task(task_id).status == "running"
    assert _chain_card(dep) is None
