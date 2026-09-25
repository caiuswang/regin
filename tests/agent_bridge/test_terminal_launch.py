"""The terminal launch kind (`lib/agent_bridge/launch.py` + `POST /api/agent-runs`).

tmux is stubbed at `attach._tmux`, the seam both modules share; the real server
was driven end to end with a live `claude` before this landed.
"""

from __future__ import annotations

import uuid

import pytest

from lib.agent_bridge import attach, launch
from lib.agent_sdk import supervisor
from lib.settings import settings


@pytest.fixture
def tmux(monkeypatch):
    state = type("S", (), {"calls": [], "command": "claude.exe",
                           "alive": False, "new_session_rc": 0})()

    class _R:
        def __init__(self, rc=0, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def _fake(*args):
        state.calls.append(args)
        if args[0] == "new-session":
            if state.new_session_rc == 0:
                state.alive = True
            return _R(state.new_session_rc, err="refused")
        if args[0] == "list-panes":
            return (_R(0, f"%9\t4242\t4243\t{state.command}\n")
                    if state.alive else _R(1))
        if args[0] == "kill-session":
            state.alive = False
            return _R(0)
        if args[0] == "display-message":
            return _R(0, "/tmp/tmux-501/regin-bridge\n")
        return _R(0)

    monkeypatch.setattr(attach, "_tmux", _fake)
    monkeypatch.setattr(launch.shutil, "which", lambda b: f"/abs/{b}")
    monkeypatch.setattr(settings.agent_bridge, "enabled", True)
    monkeypatch.setattr(settings.agent_bridge, "terminal_enabled", True)
    return state


def _verbs(state):
    return [c[0] for c in state.calls]


def _row(trace_id):
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT pane_id, tmux_socket, reachable, cwd FROM bridge_panes "
            "WHERE trace_id = ?", (trace_id,)).fetchone()
    finally:
        conn.close()


def _new_session_argv(state):
    return next(c for c in state.calls if c[0] == "new-session")


def _launch_full(flask_client, cwd):
    res = flask_client.post("/api/agent-runs", json={
        "kind": "terminal", "cwd": str(cwd), "model": "opus",
        "permission_mode": "plan", "prompt": "hi"})
    assert res.status_code == 200
    return res.get_json()


def test_launch_replies_with_a_regin_chosen_uuid(flask_client, tmp_path, tmux):
    body = _launch_full(flask_client, tmp_path)

    assert body["launched"] is True
    assert body["kind"] == "terminal"
    assert str(uuid.UUID(body["trace_id"])) == body["trace_id"]


def test_launch_passes_the_fields_to_the_claude_argv(flask_client, tmp_path,
                                                     tmux):
    trace_id = _launch_full(flask_client, tmp_path)["trace_id"]

    argv = _new_session_argv(tmux)
    cmd = list(argv[argv.index("/abs/claude"):])
    assert argv[argv.index("-s") + 1] == f"term-{trace_id[:8]}"
    assert argv[argv.index("-c") + 1] == str(tmp_path)
    assert "REGIN_BRIDGE=1" in argv
    assert cmd == ["/abs/claude", "--session-id", trace_id, "--model", "opus",
                   "--permission-mode", "plan", "--", "hi"]


def test_a_dash_prompt_is_a_turn_not_a_flag(flask_client, tmux):
    flask_client.post("/api/agent-runs", json={
        "kind": "terminal", "prompt": "--dangerously-skip-permissions"})

    argv = _new_session_argv(tmux)
    assert argv[-2:] == ("--", "--dangerously-skip-permissions")


def test_a_missing_cwd_is_refused_before_spawning(flask_client, tmp_path, tmux):
    body = flask_client.post("/api/agent-runs", json={
        "kind": "terminal", "cwd": str(tmp_path / "nope")}).get_json()

    assert body["launched"] is False and "no such directory" in body["detail"]
    assert tmux.calls == []


def test_launch_records_the_pane_before_replying(flask_client, tmp_path, tmux):
    trace_id = _launch_full(flask_client, tmp_path)["trace_id"]

    row = _row(trace_id)
    assert dict(row) == {"pane_id": "%9", "reachable": 1, "cwd": str(tmp_path),
                         "tmux_socket": "/tmp/tmux-501/regin-bridge"}


def test_terminal_kind_does_not_need_the_sdk_tier(flask_client, tmux,
                                                  monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", False)

    body = flask_client.post("/api/agent-runs", json={"kind": "terminal"}).get_json()

    assert body["launched"] is True


@pytest.mark.parametrize("flag,detail", [
    ("enabled", "bridge disabled"),
    ("terminal_enabled", "terminal disabled"),
])
def test_disabled_switches_refuse_before_spawning(flask_client, tmux,
                                                  monkeypatch, flag, detail):
    monkeypatch.setattr(settings.agent_bridge, flag, False)

    res = flask_client.post("/api/agent-runs", json={"kind": "terminal"})

    assert res.get_json() == {"launched": False, "detail": detail}
    assert tmux.calls == []


def test_an_editor_is_refused_the_terminal(flask_client, tmux):
    from lib.auth import create_token
    token = create_token(2, "ed", "editor")

    res = flask_client.post("/api/agent-runs", json={"kind": "terminal"},
                            headers={"Authorization": f"Bearer {token}"})

    assert res.get_json() == {"launched": False,
                              "detail": "admin role required"}
    assert tmux.calls == []


def test_a_missing_binary_is_named(flask_client, tmux, monkeypatch):
    monkeypatch.setattr(launch.shutil, "which",
                        lambda b: None if b == "claude" else f"/abs/{b}")

    body = flask_client.post("/api/agent-runs", json={"kind": "terminal"}).get_json()

    assert body["launched"] is False and "claude" in body["detail"]
    assert tmux.calls == []


def test_one_shot_is_a_400(flask_client, tmux):
    res = flask_client.post("/api/agent-runs",
                            json={"kind": "terminal", "one_shot": True})

    assert res.status_code == 400
    assert "not supported for terminal sessions" in res.get_json()["error"]
    assert tmux.calls == []


# ── resume ────────────────────────────────────────────────────────────

_CHILD = "11111111-2222-3333-4444-555555555555"
_RUN = "sdk-termres01"


def _cmd(state):
    argv = _new_session_argv(state)
    return list(argv[argv.index("/abs/claude"):])


def test_resume_reopens_the_session_under_its_own_id(flask_client, tmp_path,
                                                      tmux):
    body = flask_client.post("/api/agent-runs", json={
        "kind": "terminal", "resume": _CHILD, "cwd": str(tmp_path),
        "prompt": "carry on"}).get_json()

    assert body == {"launched": True, "trace_id": _CHILD, "kind": "terminal"}
    assert _cmd(tmux) == ["/abs/claude", "--resume", _CHILD, "--", "carry on"]
    argv = _new_session_argv(tmux)
    assert argv[argv.index("-s") + 1] == f"term-{_CHILD[:8]}"
    assert _row(_CHILD)["pane_id"] == "%9"


def test_resuming_a_stopped_run_reopens_its_cli_child(flask_client, tmux):
    from lib.agent_sdk import store
    store.upsert_run(_RUN, status="exited")
    store.set_cli_session(_RUN, _CHILD)

    body = flask_client.post("/api/agent-runs", json={
        "kind": "terminal", "resume": _RUN}).get_json()

    assert body["trace_id"] == _CHILD
    assert _cmd(tmux)[:3] == ["/abs/claude", "--resume", _CHILD]


def test_a_live_run_is_not_resumed_into_a_pane(flask_client, tmux):
    from lib.agent_sdk import registry, store
    store.upsert_run(_RUN, status="running")
    store.set_cli_session(_RUN, _CHILD)
    registry.register_run(_RUN, object())
    try:
        res = flask_client.post("/api/agent-runs",
                                json={"kind": "terminal", "resume": _RUN})
    finally:
        registry.unregister_run(_RUN)

    assert res.status_code == 400
    assert tmux.calls == []


@pytest.mark.parametrize("resume", [_CHILD, _RUN])
def test_a_session_live_in_a_terminal_is_not_reopened(flask_client, tmux,
                                                      monkeypatch, resume):
    """Either id path: a stopped run's child may itself be running in a pane
    this kind opened earlier."""
    from lib.agent_bridge import delivery
    from lib.agent_sdk import store
    store.upsert_run(_RUN, status="exited")
    store.set_cli_session(_RUN, _CHILD)
    monkeypatch.setattr(delivery, "session_is_live", lambda tid: tid == _CHILD)

    res = flask_client.post("/api/agent-runs",
                            json={"kind": "terminal", "resume": resume})

    assert res.status_code == 400
    assert "live in a terminal" in res.get_json()["error"]
    assert tmux.calls == []


@pytest.mark.parametrize("bad", ["--dangerously-skip-permissions", "a.b:c"])
def test_a_resume_id_that_is_not_a_uuid_never_reaches_argv(flask_client, tmux,
                                                            bad):
    body = flask_client.post("/api/agent-runs", json={
        "kind": "terminal", "resume": bad}).get_json()

    assert body["launched"] is False
    assert "not a CLI session id" in body["detail"]
    assert tmux.calls == []


def test_a_resume_cannot_move_into_a_worktree(flask_client, tmux):
    res = flask_client.post("/api/agent-runs", json={
        "kind": "terminal", "resume": _CHILD,
        "worktree": {"branch": "feat/x", "base": ""}})

    assert res.status_code == 400
    assert tmux.calls == []


def test_an_unknown_kind_is_a_400(flask_client, tmux):
    res = flask_client.post("/api/agent-runs", json={"kind": "pty"})

    assert res.status_code == 400
    assert tmux.calls == []


def test_a_pane_that_is_not_claude_is_torn_down(flask_client, tmux):
    tmux.command = "zsh"

    body = flask_client.post("/api/agent-runs", json={"kind": "terminal"}).get_json()

    assert body["launched"] is False and "zsh" in body["detail"]
    assert "kill-session" in _verbs(tmux) and tmux.alive is False
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM bridge_panes "
                            "WHERE reachable = 1").fetchone()[0] == 0
    finally:
        conn.close()


def test_a_pane_that_is_already_gone_is_released(flask_client, tmux,
                                                 monkeypatch):
    real = attach._tmux

    def _dies(*args):
        r = real(*args)
        if args[0] == "new-session":
            tmux.alive = False
        return r

    monkeypatch.setattr(attach, "_tmux", _dies)

    body = flask_client.post("/api/agent-runs", json={"kind": "terminal"}).get_json()

    assert body["launched"] is False
    assert "kill-session" in _verbs(tmux)


def test_tmux_refusal_is_structured(flask_client, tmux):
    tmux.new_session_rc = 1

    body = flask_client.post("/api/agent-runs", json={"kind": "terminal"}).get_json()

    assert body == {"launched": False, "detail": "tmux refused: refused"}


def test_omitted_kind_is_the_sdk_launch(flask_client, tmux, monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(supervisor, "launch", lambda prompt, **kw: "sdk-x")

    body = flask_client.post("/api/agent-runs", json={"prompt": "go"}).get_json()

    assert body == {"launched": True, "trace_id": "sdk-x"}
    assert tmux.calls == []


def test_launch_options_report_terminal_availability(flask_client, tmux,
                                                     monkeypatch):
    opts = flask_client.get("/api/agent-runs/launch-options").get_json()
    assert opts["terminal_available"] is True and opts["terminal_detail"] == ""

    monkeypatch.setattr(settings.agent_bridge, "terminal_enabled", False)
    opts = flask_client.get("/api/agent-runs/launch-options").get_json()
    assert opts["terminal_available"] is False
    assert opts["terminal_detail"] == "terminal disabled"
