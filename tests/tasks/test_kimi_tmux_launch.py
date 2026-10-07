"""Feature #140 S5 — a task run launched through a provider's tmux pane.

The provider owns the pane: its argv, the session id the pane runs under, and
how the first turn reaches the composer. Kimi anchors over ACP (D5), resumes
that session in the pane, and has its prompt pasted and submitted with C-m.

tmux is stubbed at both seams — `attach._tmux` (spawn, identity, liveness)
and `pane_io._tmux` (delivery) — and share one pane. The last test drives the
real `kimi` in a real tmux and spends an API call.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.agent_acp import client as acp_client
from lib.agent_bridge import attach, delivery, launch, pane_io, store
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask, AgentTaskRun
from lib.providers import kimi as kimi_mod
from lib.providers.registry import build_provider
from lib.settings import settings
from lib.tasks import runs, tmux_runs

_KIMI_ID = "session_5f0c2d1e-0000-4000-8000-00000000c0de"


class _R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


@pytest.fixture
def pane(monkeypatch, tmp_path):
    """One fake tmux server: a session spawns a pane running `command`, whose
    composer shows what was pasted as kimi renders it (a chip)."""
    state = SimpleNamespace(calls=[], keys=[], live=set(), command="node",
                            pasted=[], echo=True, ready=True, submit_rc=0)

    def _attach_tmux(*args):
        state.calls.append(args)
        verb = args[0]
        if verb == "new-session":
            state.live.add(args[args.index("-s") + 1])
            return _R(0)
        if verb == "list-panes":
            return _R(0, f"%9\t4242\t4243\t{state.command}\n")
        if verb == "has-session":
            name = args[args.index("-t") + 1].lstrip("=")
            return (_R(0) if name in state.live
                    else _R(1, err=f"can't find session: {name}\n"))
        if verb == "kill-session":
            state.live.discard(args[args.index("-t") + 1])
            return _R(0)
        if verb == "display-message":
            return _R(0, "/tmp/tmux-501/regin-bridge\n")
        return _R(0)

    def _pane_tmux(_socket, *args):
        verb = args[0]
        if verb == "display-message":
            return _R(0, f"4242\t4243\t{state.command}\t0\n")
        if verb == "capture-pane":
            if not state.ready:
                return _R(0, " Starting…\n")
            shown = (f"[paste #{len(state.pasted)} +40 lines]"
                     if state.pasted and state.echo else "")
            return _R(0, f" ╭──╮\n │ > {shown}\n ╰──╯\n")
        if verb == "send-keys":
            state.keys.append(args)
            if "-l" in args:
                state.pasted.append(args[-1])
            elif args[-1] == "C-m":
                return _R(state.submit_rc, err="lost")
        return _R(0)

    monkeypatch.setattr(attach, "_tmux", _attach_tmux)
    monkeypatch.setattr(pane_io, "_tmux", _pane_tmux)
    monkeypatch.setattr(delivery, "_tmux", _pane_tmux)
    monkeypatch.setattr(pane_io.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(delivery.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(shutil, "which", lambda b, *a, **k: f"/abs/{b}")
    monkeypatch.setattr(settings.agent_bridge, "enabled", True)
    monkeypatch.setattr(kimi_mod, "_WORKSPACE_TRUST_DIR", tmp_path / "trust")
    return state


@pytest.fixture
def acp(monkeypatch, pane):
    """The ACP anchor's client, recording its lifecycle into the pane's
    call log so the order against the spawn is checkable."""
    state = SimpleNamespace(cwds=[], fail=None)

    class _FakeAcp:
        def __init__(self, cmd=None, env=None, cwd=None):
            self.env = env

        def __enter__(self):
            pane.calls.append(("acp-connect",))
            return self

        def __exit__(self, *exc):
            pane.calls.append(("acp-close",))

        def initialize(self, timeout=None):
            return {}

        def session_new(self, cwd, timeout=None):
            if state.fail is not None:
                raise state.fail
            state.cwds.append(cwd)
            pane.calls.append(("acp-session-new", cwd))
            return _KIMI_ID

    monkeypatch.setattr(acp_client, "AcpClient", _FakeAcp)
    return state


@pytest.fixture
def no_other_launch(monkeypatch):
    """Neither the SDK nor a provider channel may launch a tmux run."""
    calls = []
    monkeypatch.setattr(runs.supervisor, "launch_run",
                        lambda *a, **k: calls.append(("sdk", a, k)))
    monkeypatch.setattr(runs, "_channel_launcher",
                        lambda pid: calls.append(("channel", pid)))
    return calls


def _armed(client, title="a task", **params):
    resp = client.post("/api/tasks", json={"title": title})
    assert resp.status_code == 201, resp.get_json()
    task_id = resp.get_json()["task"]["id"]
    resp = client.post(f"/api/tasks/{task_id}/arm", json=params)
    assert resp.status_code == 200, resp.get_json()
    return task_id


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


def _pane_command(call) -> list[str]:
    """The command tmux was told to run: what follows the last `-e` pair."""
    args = list(call)
    last_env = max(i for i, a in enumerate(args) if a == "-e")
    return args[last_env + 2:]


# ── argv ─────────────────────────────────────────────────────

def test_kimi_launch_argv_contains_no_claude_flags(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda b, *a, **k: f"/abs/{b}")

    argv = build_provider("kimi").pane_argv(
        _KIMI_ID, model="kimi-k2", effort="high", permission_mode="plan",
        prompt="--do the work")

    assert argv == ["/abs/kimi", f"--session={_KIMI_ID}", "--yolo",
                    "--model=kimi-k2"]
    for claude_only in ("--session-id", "--resume", "--effort",
                        "--permission-mode", "--", "--do the work"):
        assert claude_only not in argv
    assert build_provider("kimi").pane_argv(_KIMI_ID) == [
        "/abs/kimi", f"--session={_KIMI_ID}", "--yolo"]


def test_claude_tmux_argv_unchanged(flask_client, pane, no_other_launch):
    claude = build_provider("claude")
    assert claude.pane_input() is None
    assert claude.pane_argv("t-1", model="opus", effort="high",
                            permission_mode="plan", prompt="- a bullet") == [
        "/abs/claude", "--session-id", "t-1", "--model", "opus",
        "--effort", "high", "--permission-mode", "plan", "--", "- a bullet"]
    assert claude.pane_argv("t-1", resume=True) == [
        "/abs/claude", "--resume", "t-1"]

    task_id = _armed(flask_client, launch="tmux", model="opus",
                     permission_mode="plan")
    run = _run(flask_client, task_id)

    (call,) = [c for c in pane.calls if c[0] == "new-session"]
    command = _pane_command(call)
    assert uuid.UUID(run["trace_id"])
    assert command[:-1] == ["/abs/claude", "--session-id", run["trace_id"],
                            "--model", "opus", "--permission-mode", "plan",
                            "--"]
    assert f"task finish {run['id']}" in command[-1]
    # The prompt rode on argv: nothing typed, no ACP anchor, no other launch.
    assert (pane.keys, pane.pasted, no_other_launch) == ([], [], [])
    assert [c[0] for c in pane.calls if c[0].startswith("acp")] == []


# ── launch ───────────────────────────────────────────────────

def test_task_run_tmux_launch_uses_provider_argv(flask_client, pane, acp,
                                                 no_other_launch, tmp_path):
    task_id = _armed(flask_client, launch="tmux", provider="kimi",
                     model="kimi-k2", cwd=str(tmp_path))

    run = _run(flask_client, task_id)

    (call,) = [c for c in pane.calls if c[0] == "new-session"]
    assert call[:4] == ("new-session", "-d", "-s", f"task-{task_id}-a1")
    assert call[call.index("-c") + 1] == str(tmp_path)
    assert _pane_command(call) == ["/abs/kimi", f"--session={_KIMI_ID}",
                                   "--yolo", "--model=kimi-k2"]
    assert no_other_launch == []
    assert run["status"] == "running"
    assert _task(task_id).status == "running"


def test_kimi_tmux_refused_when_kimi_is_missing(flask_client, pane, acp,
                                                monkeypatch):
    task_id = _armed(flask_client, launch="tmux", provider="kimi")
    monkeypatch.setattr(shutil, "which",
                        lambda b, *a, **k: None if b == "kimi" else f"/abs/{b}")

    resp = flask_client.post(f"/api/tasks/{task_id}/run")

    assert resp.status_code == 409
    assert "kimi not found on the server's PATH" in resp.get_json()["error"]
    assert not [c for c in pane.calls if c[0] == "new-session"]
    assert _task(task_id).status == "armed"


def test_kimi_tmux_session_anchor_is_real_session_id(flask_client, pane, acp,
                                                     tmp_path):
    task_id = _armed(flask_client, launch="tmux", provider="kimi",
                     cwd=str(tmp_path))

    run = _run(flask_client, task_id)

    assert run["trace_id"] == _KIMI_ID
    assert _attempt(run["id"]).trace_id == _KIMI_ID
    assert acp.cwds == [str(tmp_path)]
    verbs = [c[0] for c in pane.calls]
    # The channel is closed before the pane resumes the session it made.
    assert verbs.index("acp-session-new") < verbs.index("acp-close") \
        < verbs.index("new-session")
    assert (tmp_path / "trust").is_dir()
    assert store.get_reachable_pane(_KIMI_ID)["pane_id"] == "%9"


def test_kimi_tmux_anchor_uses_the_resolved_cwd(flask_client, pane, acp,
                                               tmp_path):
    # The pane's kimi sees its cwd resolved, and will not resume a session
    # created under the symlinked spelling of the same directory.
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    task_id = _armed(flask_client, launch="tmux", provider="kimi",
                     cwd=str(link))

    _run(flask_client, task_id)

    assert acp.cwds == [os.path.realpath(real)]


def test_kimi_tmux_anchor_failure_settles_the_attempt(flask_client, pane, acp):
    acp.fail = acp_client.AcpError("auth required")
    task_id = _armed(flask_client, launch="tmux", provider="kimi")

    resp = flask_client.post(f"/api/tasks/{task_id}/run")

    assert resp.status_code == 409
    error = resp.get_json()["error"]
    assert "no kimi session" in error and "auth required" in error
    assert not [c for c in pane.calls if c[0] == "new-session"]
    assert _task(task_id).status == "armed"


# ── first turn ───────────────────────────────────────────────

def test_kimi_tmux_prompt_submitted_with_c_m(flask_client, pane, acp,
                                            monkeypatch):
    # Far below the prompt's length: the first turn must not be capped,
    # because the finish instruction is its last line.
    monkeypatch.setattr(settings.agent_bridge, "max_text_len", 50)
    task_id = _armed(flask_client, launch="tmux", provider="kimi")

    run = _run(flask_client, task_id)

    (paste,) = pane.pasted
    assert paste.startswith("\x1b[200~") and paste.endswith("\x1b[201~")
    assert f"task finish {run['id']}" in paste
    submits = [k for k in pane.keys if "-l" not in k]
    assert submits[-1] == ("send-keys", "-t", "%9", "C-m")
    assert not any("Enter" in k for k in pane.keys)
    assert pane.keys.index(submits[-1]) > pane.keys.index(
        next(k for k in pane.keys if "-l" in k))


def test_kimi_tmux_unseen_paste_is_not_submitted(flask_client, pane, acp):
    pane.echo = False
    task_id = _armed(flask_client, launch="tmux", provider="kimi")

    resp = flask_client.post(f"/api/tasks/{task_id}/run")

    assert resp.status_code == 409
    assert "first turn not delivered" in resp.get_json()["error"]
    assert not any(k[-1] == "C-m" for k in pane.keys)
    assert f"task-{task_id}-a1" not in pane.live
    assert _task(task_id).status == "armed"


def test_kimi_tmux_composer_never_drawn_is_refused(flask_client, pane, acp):
    pane.ready = False
    task_id = _armed(flask_client, launch="tmux", provider="kimi")

    resp = flask_client.post(f"/api/tasks/{task_id}/run")

    assert resp.status_code == 409
    assert "composer never appeared" in resp.get_json()["error"]
    assert pane.pasted == []


def test_kimi_tmux_failed_submit_is_refused(flask_client, pane, acp):
    pane.submit_rc = 1
    task_id = _armed(flask_client, launch="tmux", provider="kimi")

    resp = flask_client.post(f"/api/tasks/{task_id}/run")

    assert resp.status_code == 409
    assert "submit failed: lost" in resp.get_json()["error"]
    assert f"task-{task_id}-a1" not in pane.live


# ── pane guard ───────────────────────────────────────────────

def test_pane_guard_allows_provider_declared_binaries(flask_client, pane, acp,
                                                      monkeypatch):
    from hook_manager.handlers import bridge_registry

    monkeypatch.setattr(settings.agent_bridge, "allowed_pane_commands",
                        ["claude"])
    declared = dict(build_provider("kimi").launch_capabilities(),
                    pane_commands=["kimi-native"])
    monkeypatch.setattr(kimi_mod.KimiProvider, "launch_capabilities",
                        lambda self: declared)
    pane.command = "kimi-native"

    assert {"claude", "kimi-native"} <= pane_io.allowed_pane_commands()
    assert bridge_registry._command_allowed("kimi-native")
    row = {"tmux_server_pid": 1, "pane_pid": 2}
    assert pane_io._match_identity(row, "1", "2", "kimi-native") is None

    task_id = _armed(flask_client, launch="tmux", provider="kimi")
    assert _run(flask_client, task_id)["trace_id"] == _KIMI_ID

    # A shell is still refused everywhere.
    assert "fish" not in pane_io.allowed_pane_commands()
    assert not bridge_registry._command_allowed("fish")
    assert "refused" in pane_io._match_identity(row, "1", "2", "fish")
    pane.command = "fish"
    other = _armed(flask_client, "a second task", launch="tmux",
                   provider="kimi")
    resp = flask_client.post(f"/api/tasks/{other}/run")
    assert resp.status_code == 409
    assert "pane is running 'fish', not the CLI" in resp.get_json()["error"]


# ── real kimi ────────────────────────────────────────────────

@pytest.mark.skipif(not (shutil.which("kimi") and shutil.which("tmux")),
                    reason="needs the kimi CLI and a real tmux")
def test_real_kimi_tmux_run_settles_via_finish(flask_client, tmp_path,
                                               monkeypatch):
    """The real `kimi`, anchored over ACP and resumed in a real pane: the
    attempt runs under kimi's own session id, survives an app restart with
    its pane LIVE, and settles done through its own `task finish`.
    Gated behind REGIN_E2E_KIMI=1 — it spends a real API call."""
    if os.environ.get("REGIN_E2E_KIMI") != "1":
        pytest.skip("set REGIN_E2E_KIMI=1 to spend a real API call")
    from lib.orm import engine
    from web.app import create_app

    socket = f"regin-test-{uuid.uuid4().hex[:8]}"
    (tmp_path / "repo").mkdir()
    # Armed through a symlink: kimi only resumes from the resolved path.
    workdir = tmp_path / "link"
    workdir.symlink_to(tmp_path / "repo")
    trace_id = ""
    # The real kimi reads the real trust dir, which conftest isolates; the
    # record this run adds is removed again below.
    trust = Path.home() / ".kimi-code" / "workspace-trust"
    before = set(trust.glob("wd_repo_*"))
    monkeypatch.setattr(kimi_mod, "_WORKSPACE_TRUST_DIR", trust)
    monkeypatch.setattr(settings.agent_bridge, "enabled", True)
    monkeypatch.setattr(settings.agent_bridge, "attach_socket", socket)
    # The pane reaches this test's DB only through its env; with nothing on
    # :9 its `task finish` settles in its own process.
    env = {"REGIN_TRACE_DB_PATH": engine.DB_PATH,
           "REGIN_AGENT_MEMORY__INJECT_SERVER_URL": "http://127.0.0.1:9"}
    try:
        task_id = _armed(
            flask_client,
            "Do no other work: run the finish command below exactly as "
            "given, with --reason 'e2e ok'.",
            launch="tmux", provider="kimi", cwd=str(workdir), env=env)
        run = _run(flask_client, task_id)
        trace_id = run["trace_id"]
        assert trace_id.startswith("session_")
        listed = subprocess.run(
            ["kimi", "session", "list", "--cwd", os.path.realpath(workdir),
             "--json"], capture_output=True, text=True, timeout=60)
        assert trace_id in {s["id"] for s in json.loads(listed.stdout)}

        create_app()  # the restart: a fresh app runs the boot reap
        view = {"task_id": task_id, "attempt": run["attempt"]}
        assert tmux_runs.liveness(view) == tmux_runs.LIVE
        assert _attempt(run["id"]).status == "running"

        deadline = time.monotonic() + 240
        while _attempt(run["id"]).status == "running":
            assert time.monotonic() < deadline, subprocess.run(
                ["tmux", "-L", socket, "capture-pane", "-p", "-t",
                 f"task-{task_id}-a1"], capture_output=True,
                text=True).stdout
            time.sleep(1)
        assert _attempt(run["id"]).status == "done"
        assert _task(task_id).status == "done"
    finally:
        subprocess.run(["tmux", "-L", socket, "kill-server"],
                       capture_output=True)
        for record in set(trust.glob("wd_repo_*")) - before:
            record.unlink()
        # A failed delete must not mask the assertion that failed the test.
        if trace_id:
            with (contextlib.suppress(Exception),
                  acp_client.AcpClient(cwd=os.path.realpath(workdir)) as acp):
                acp.initialize()
                acp._request("session/delete", {"sessionId": trace_id},
                             timeout=30)
