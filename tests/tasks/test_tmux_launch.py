"""Feature #120 — task runs launched in a detached tmux session.

tmux is stubbed at `attach._tmux`, the seam `launch.spawn_detached` and
`tmux_runs` share; the fake keeps a set of live session names so `has-session`
answers the way tmux does. The restart test at the bottom drives a real tmux.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import uuid
from types import SimpleNamespace

import pytest

from lib.agent_bridge import attach, launch
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask, AgentTaskRun
from lib.settings import settings
from lib.tasks import runs, tmux_runs


class _R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


@pytest.fixture
def tmux(monkeypatch):
    state = SimpleNamespace(calls=[], live=set(), server=True,
                            has_session_error=None)

    def _fake(*args):
        state.calls.append(args)
        verb = args[0]
        if verb == "new-session":
            state.live.add(args[args.index("-s") + 1])
            return _R(0)
        if verb == "list-panes":
            return _R(0, "%9\t4242\t4243\tclaude\n")
        if verb == "has-session":
            if state.has_session_error is not None:
                raise state.has_session_error
            if not state.server:
                return _R(1, err="no server running on /tmp/tmux-501/x\n")
            name = args[args.index("-t") + 1].lstrip("=")
            return (_R(0) if name in state.live
                    else _R(1, err=f"can't find session: {name}\n"))
        if verb == "kill-session":
            name = args[args.index("-t") + 1]
            if name in state.live:
                state.live.discard(name)
                return _R(0)
            return _R(1, err="can't find session")
        if verb == "display-message":
            return _R(0, "/tmp/tmux-501/regin-bridge\n")
        return _R(0)

    monkeypatch.setattr(attach, "_tmux", _fake)
    monkeypatch.setattr(launch.shutil, "which", lambda b: f"/abs/{b}")
    monkeypatch.setattr(settings.agent_bridge, "enabled", True)
    return state


@pytest.fixture
def no_sdk(monkeypatch):
    """Any SDK launch is recorded — a tmux run must never make one."""
    calls = []

    def _fake(prompt, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(trace_id=f"sdk-{len(calls)}",
                               add_done_callback=lambda cb: None)

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return calls


def _capture(client, title="a task"):
    resp = client.post("/api/tasks", json={"title": title})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["task"]["id"]


def _armed(client, title="a task", **params):
    task_id = _capture(client, title)
    resp = client.post(f"/api/tasks/{task_id}/arm", json=params)
    assert resp.status_code == 200, resp.get_json()
    return task_id


def _after(client, task_id, dep_id):
    resp = client.put(f"/api/tasks/{task_id}/deps",
                      json={"depends_on": [dep_id]})
    assert resp.status_code == 200, resp.get_json()


def _run(client, task_id):
    resp = client.post(f"/api/tasks/{task_id}/run")
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["run"]


def _task(task_id) -> AgentTask:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


def _attempt(run_id) -> AgentTaskRun:
    with SessionLocal() as session:
        return session.get(AgentTaskRun, run_id)


def _new_sessions(state):
    return [c for c in state.calls if c[0] == "new-session"]


# ── arming ───────────────────────────────────────────────────

def test_arm_accepts_launch_mode(flask_client):
    task_id = _capture(flask_client)
    for mode in ("sdk", "tmux"):
        resp = flask_client.post(f"/api/tasks/{task_id}/arm",
                                 json={"launch": mode})
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["armed"]["params"]["launch"] == mode

    refused = flask_client.post(f"/api/tasks/{task_id}/arm",
                                json={"launch": "docker"})
    assert refused.status_code == 400
    assert refused.get_json()["field"] == "launch"

    omitted = flask_client.post(f"/api/tasks/{task_id}/arm", json={})
    assert omitted.get_json()["armed"]["params"]["launch"] == "sdk"


# ── firing ───────────────────────────────────────────────────

def test_tmux_run_starts_detached_session(flask_client, tmux, no_sdk):
    task_id = _armed(flask_client, launch="tmux", model="opus")

    run = _run(flask_client, task_id)

    assert no_sdk == []
    (call,) = _new_sessions(tmux)
    assert call[:4] == ("new-session", "-d", "-s", f"task-{task_id}-a1")
    argv = list(call)
    trace_id = argv[argv.index("--session-id") + 1]
    assert uuid.UUID(trace_id)
    assert argv[argv.index("--model") + 1] == "opus"
    # The finish instruction reaches the agent: it is how the run settles.
    assert f"task finish {run['id']}" in argv[-1]
    assert run["trace_id"] == trace_id
    assert run["status"] == "running"
    assert _task(task_id).status == "running"


def test_tmux_refusal_settles_the_attempt_and_keeps_the_task_armed(
        flask_client, tmux, monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    task_id = _armed(flask_client, launch="tmux")

    resp = flask_client.post(f"/api/tasks/{task_id}/run")

    assert resp.status_code == 409
    assert "bridge disabled" in resp.get_json()["error"]
    assert _new_sessions(tmux) == []
    assert _task(task_id).status == "armed"


def test_a_live_tmux_run_refuses_a_second_launch(flask_client, tmux):
    task_id = _armed(flask_client, launch="tmux")
    _run(flask_client, task_id)

    again = flask_client.post(f"/api/tasks/{task_id}/run")

    assert again.status_code == 409
    assert "already has a live run" in again.get_json()["error"]


# ── boot ─────────────────────────────────────────────────────

def _boot():
    from web.app import _reap_orphaned_agent_runs
    _reap_orphaned_agent_runs()


def test_startup_reap_spares_live_tmux_run(flask_client, tmux):
    task_id = _armed(flask_client, launch="tmux")
    run = _run(flask_client, task_id)

    _boot()
    assert _attempt(run["id"]).ended_at is None
    assert _task(task_id).status == "running"

    # tmux cannot answer at all: not evidence the session is gone.
    tmux.has_session_error = FileNotFoundError("tmux")
    _boot()
    assert _attempt(run["id"]).status == "running"
    assert _task(task_id).status == "running"


def test_startup_reap_fails_dead_tmux_run(flask_client, tmux, no_sdk):
    task_id = _armed(flask_client, launch="tmux")
    run = _run(flask_client, task_id)
    dependent = _armed(flask_client, "next", launch="tmux")
    _after(flask_client, dependent, task_id)
    tmux.live.clear()

    _boot()

    row = _attempt(run["id"])
    assert row.status == "failed"
    assert row.exit_reason == tmux_runs.GONE_REASON
    assert _task(task_id).status == "failed"
    # A requires edge on a failed task stays unmet; nothing launched.
    assert _task(dependent).status == "armed"
    assert len(_new_sessions(tmux)) == 1


def test_startup_reap_on_a_dead_tmux_server(flask_client, tmux):
    task_id = _armed(flask_client, launch="tmux")
    run = _run(flask_client, task_id)
    tmux.server = False

    _boot()

    assert _attempt(run["id"]).exit_reason == tmux_runs.GONE_REASON


# ── settling ─────────────────────────────────────────────────

def test_task_finish_settles_tmux_run_and_fires_dependents(
        flask_client, tmux, no_sdk):
    task_id = _armed(flask_client, launch="tmux")
    run = _run(flask_client, task_id)
    tmux_dep = _armed(flask_client, "tmux dep", launch="tmux")
    sdk_dep = _armed(flask_client, "sdk dep")
    _after(flask_client, tmux_dep, task_id)
    _after(flask_client, sdk_dep, task_id)

    resp = flask_client.post(f"/api/tasks/runs/{run['id']}/finish",
                             json={"status": "done", "reason": "built it"})

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["settled"] is True
    assert sorted(resp.get_json()["fired"]) == sorted([tmux_dep, sdk_dep])
    assert _attempt(run["id"]).status == "done"
    # The server launches both kinds.
    assert len(no_sdk) == 1
    assert len(_new_sessions(tmux)) == 2
    # A second report is a no-op.
    again = flask_client.post(f"/api/tasks/runs/{run['id']}/finish", json={})
    assert again.get_json()["settled"] is False


def test_cli_settle_with_no_server_defers_sdk_dependents_to_boot(
        flask_client, tmux, no_sdk, tmp_path):
    task_id = _armed(flask_client, launch="tmux", verify_cmd="true",
                     cwd=str(tmp_path))
    run = _run(flask_client, task_id)
    tmux_dep = _armed(flask_client, "tmux dep", launch="tmux")
    sdk_dep = _armed(flask_client, "sdk dep")
    _after(flask_client, tmux_dep, task_id)
    _after(flask_client, sdk_dep, task_id)

    result = runs.finish_run(run["id"], reason="done", in_server=False)

    # Verify ran inline — no worker thread outlives a CLI.
    assert result["settled"] is True and result["verifying"] is False
    assert _attempt(run["id"]).verify_exit_code == 0
    assert result["fired"] == [tmux_dep]
    assert no_sdk == []
    assert _task(sdk_dep).status == "armed"

    _boot()

    assert _task(sdk_dep).status == "running"
    assert len(no_sdk) == 1


def test_cli_finish_falls_back_when_nothing_listens(
        flask_client, tmux, monkeypatch):
    from typer.testing import CliRunner

    from cli.commands.tasks import tasks_app

    task_id = _armed(flask_client, launch="tmux")
    run = _run(flask_client, task_id)
    monkeypatch.setattr(settings.agent_memory, "inject_server_url",
                        "http://127.0.0.1:9")

    out = CliRunner().invoke(tasks_app, ["finish", str(run["id"]),
                                         "--reason", "done"])

    assert out.exit_code == 0, out.output
    assert _attempt(run["id"]).status == "done"


# ── controls ─────────────────────────────────────────────────

def _latest(client, task_id) -> dict:
    return client.get(f"/api/tasks/{task_id}/runs").get_json()["runs"][0]


def test_interrupt_on_tmux_run_sends_escape(flask_client, tmux, monkeypatch):
    from lib.agent_bridge import delivery

    task_id = _armed(flask_client, launch="tmux")
    run = _run(flask_client, task_id)
    keys = []
    monkeypatch.setattr(delivery, "deliver_key",
                        lambda tid, key: keys.append((tid, key))
                        or delivery.DeliveryResult(True, "sent"))

    interrupted = flask_client.post(f"/api/tasks/{task_id}/interrupt")

    assert interrupted.get_json()["delivered"] is True
    assert keys == [(run["trace_id"], "Escape")]
    assert _attempt(run["id"]).status == "running"


def test_stop_and_watch_on_tmux_run(flask_client, tmux):
    task_id = _armed(flask_client, launch="tmux")
    run = _run(flask_client, task_id)
    latest = _latest(flask_client, task_id)
    assert latest["live"] is True
    assert latest["trace_id"] == run["trace_id"]

    stopped = flask_client.post(f"/api/tasks/{task_id}/stop")

    assert stopped.get_json()["delivered"] is True
    assert ("kill-session", "-t", f"task-{task_id}-a1") in tmux.calls
    row = _attempt(run["id"])
    assert row.status == "failed" and row.exit_reason == "stopped"
    assert _task(task_id).status == "failed"
    assert _latest(flask_client, task_id)["live"] is False


# ── the real thing ───────────────────────────────────────────

_STUB = """\
#!{python}
import pathlib, subprocess, sys, time
prompt = sys.argv[-1]
line = next(l for l in prompt.splitlines() if " task finish " in l)
cmd = line[line.index("{python}"):].split(" --reason")[0].strip().strip("`")
gate = pathlib.Path({gate!r})
while not gate.exists():
    time.sleep(0.05)
subprocess.run(cmd + " --reason restarted", shell=True, check=True)
pathlib.Path({done!r}).write_text("ok")
time.sleep(30)
"""


@pytest.mark.skipif(shutil.which("tmux") is None, reason="needs a real tmux")
def test_tmux_run_survives_server_restart(flask_client, tmp_path, monkeypatch):
    """A real detached session outlives the app that launched it, a second
    app boot leaves it running, and its own `task finish` settles it done."""
    from web.app import create_app

    socket = f"regin-test-{uuid.uuid4().hex[:8]}"
    gate, done = tmp_path / "gate", tmp_path / "done"
    stub = tmp_path / "claude"
    stub.write_text(_STUB.format(python=sys.executable, gate=str(gate),
                                 done=str(done)))
    stub.chmod(0o755)
    monkeypatch.setattr(settings.agent_bridge, "enabled", True)
    monkeypatch.setattr(settings.agent_bridge, "attach_socket", socket)
    monkeypatch.setattr(settings.agent_bridge, "allowed_pane_commands",
                        [*settings.agent_bridge.allowed_pane_commands,
                         os.path.basename(os.path.realpath(sys.executable)),
                         "Python", "python", "python3", "claude"])
    real_which = shutil.which
    monkeypatch.setattr(launch.shutil, "which",
                        lambda b: str(stub) if b == "claude" else real_which(b))
    # The pane is another process: it reaches this test's DB only through the
    # env, and — nothing listening on :9 — settles in its own process, the
    # path a run takes while the server is down.
    from lib.orm import engine
    pane_env = {"REGIN_TRACE_DB_PATH": engine.DB_PATH,
                "REGIN_AGENT_MEMORY__INJECT_SERVER_URL": "http://127.0.0.1:9"}
    try:
        task_id = _armed(flask_client, launch="tmux", cwd=str(tmp_path),
                         env=pane_env)
        run = _run(flask_client, task_id)

        create_app()  # the restart: a fresh app runs the boot reap
        assert _attempt(run["id"]).status == "running"

        gate.write_text("go")
        _wait_for(done)
        assert _attempt(run["id"]).status == "done"
        assert _task(task_id).status == "done"
    finally:
        subprocess.run(["tmux", "-L", socket, "kill-server"],
                       capture_output=True)


def _wait_for(path, limit=60.0):
    import time
    deadline = time.monotonic() + limit
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.1)


# ── verifier findings ────────────────────────────────────────

def test_a_deferred_sdk_dependent_fires_on_the_servers_next_fire(
        flask_client, tmux, no_sdk):
    """The CLI settled after the new server's boot pass already ran: the
    next fire the server handles launches the deferred dependent (D5)."""
    task_id = _armed(flask_client, launch="tmux")
    run = _run(flask_client, task_id)
    sdk_dep = _armed(flask_client, "sdk dep")
    _after(flask_client, sdk_dep, task_id)
    other = _armed(flask_client, "unrelated", launch="tmux")
    other_run = _run(flask_client, other)

    runs.finish_run(run["id"], reason="done", in_server=False)
    assert _task(sdk_dep).status == "armed"

    flask_client.post(f"/api/tasks/runs/{other_run['id']}/finish", json={})

    assert _task(sdk_dep).status == "running"
    assert len(no_sdk) == 1


def test_boot_leaves_a_refused_undeferred_task_armed(flask_client, tmux,
                                                     no_sdk, monkeypatch):
    task_id = _armed(flask_client, launch="tmux")
    run = _run(flask_client, task_id)
    dep = _armed(flask_client, "dep", launch="tmux")
    _after(flask_client, dep, task_id)
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    flask_client.post(f"/api/tasks/runs/{run['id']}/finish", json={})
    assert _task(dep).status == "armed"
    monkeypatch.setattr(settings.agent_bridge, "enabled", True)

    _boot()

    assert _task(dep).status == "armed"


def test_boot_reruns_a_verify_the_previous_server_died_in(
        flask_client, tmux, tmp_path):
    from sqlalchemy import update

    from lib.tasks import verify

    task_id = _armed(flask_client, launch="tmux", verify_cmd="true",
                     cwd=str(tmp_path))
    run = _run(flask_client, task_id)
    with SessionLocal() as session:
        session.execute(update(AgentTaskRun)
                        .where(AgentTaskRun.id == run["id"])
                        .values(verify_exit_code=verify.NOT_RUN))
        session.commit()

    _boot()
    runs.wait_for_verify(run["id"])

    row = _attempt(run["id"])
    assert row.status == "done" and row.verify_exit_code == 0


def test_a_stopped_run_tmux_cannot_answer_for_is_runnable_again(
        flask_client, tmux, no_sdk):
    task_id = _armed(flask_client, launch="tmux")
    _run(flask_client, task_id)
    flask_client.post(f"/api/tasks/{task_id}/stop")
    tmux.has_session_error = FileNotFoundError("tmux")

    again = flask_client.post(f"/api/tasks/{task_id}/run")

    assert again.status_code != 409, again.get_json()


def test_boot_leaves_a_verify_the_cli_is_running_alone(flask_client, tmux,
                                                       tmp_path, monkeypatch):
    """A CLI settling while no server is up claims the verify as its own; a
    server booting mid-verify must not start a second copy in the same tree."""
    task_id = _armed(flask_client, launch="tmux", verify_cmd="true",
                     cwd=str(tmp_path))
    run = _run(flask_client, task_id)
    started = []
    real = runs._settle_and_fire

    def _boot_mid_verify(*args, **kwargs):
        _boot()
        started.append(runs._verifying.get(run["id"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(runs, "_settle_and_fire", _boot_mid_verify)

    runs.finish_run(run["id"], reason="done", in_server=False)

    assert started == [None]
    assert _attempt(run["id"]).status == "done"


def test_internal_marks_never_reach_a_launched_attempt(flask_client, tmux,
                                                       no_sdk):
    task_id = _armed(flask_client, launch="tmux")
    run = _run(flask_client, task_id)
    sdk_dep = _armed(flask_client, "sdk dep")
    _after(flask_client, sdk_dep, task_id)
    runs.finish_run(run["id"], reason="done", in_server=False)

    detail = flask_client.get(f"/api/tasks/{sdk_dep}").get_json()
    assert "fire_deferred" not in str(detail)
    started = _run(flask_client, sdk_dep)

    assert "fire_deferred" not in started["params"]
    with SessionLocal() as session:
        assert "fire_deferred" not in session.get(
            AgentTaskRun, started["id"]).params_json


def test_a_gone_run_with_a_claimed_verify_settles_with_the_not_run_code(
        flask_client, tmux, tmp_path):
    from sqlalchemy import update

    from lib.tasks import verify

    task_id = _armed(flask_client, launch="tmux", verify_cmd="true",
                     cwd=str(tmp_path))
    run = _run(flask_client, task_id)
    with SessionLocal() as session:
        session.execute(update(AgentTaskRun)
                        .where(AgentTaskRun.id == run["id"])
                        .values(verify_exit_code=verify.NOT_RUN))
        session.commit()
    tmux.live.clear()

    _boot()

    row = _attempt(run["id"])
    assert row.status == "failed"
    assert row.verify_exit_code == verify.NOT_RUN
