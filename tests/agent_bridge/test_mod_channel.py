"""Agent bridge over the Mod channel (feature 134, S2).

A session whose regin-bridge mod holds `GET /api/bridge/listen` open is
delivered to through that stream instead of its tmux pane (I3), only its own
prompts reach it (I4), and it stops being a target the moment the stream ends
(I2). The last test runs the real `regin bridge listen` child against a real
server socket, so the EOF-driven unregistration is exercised as shipped.
"""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from lib.agent_bridge import delivery, mod_channel
from lib.settings import settings

_TOKEN = "mod-channel-token"
_REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _bridge_on(monkeypatch):
    delivery._HISTORY.clear()
    cfg = settings.agent_bridge
    monkeypatch.setattr(cfg, "enabled", True)
    monkeypatch.setattr(cfg, "token", _TOKEN)
    monkeypatch.setattr(cfg, "rate_limit_per_minute", 30)
    monkeypatch.setattr(cfg, "allowed_pane_commands", ["claude"])
    monkeypatch.setattr(delivery.time, "sleep", lambda *_a, **_k: None)
    yield
    assert mod_channel._listeners == {}, "a test leaked a registered listener"


def _install_tmux(monkeypatch, capture: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def _fake_run(cmd, **_kw):
        calls.append(list(cmd))
        if "display-message" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="111\t222\tclaude\t0", stderr="")
        out = capture if "capture-pane" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    row = {"pane_id": "%7", "tmux_socket": None, "tmux_server_pid": 111, "pane_pid": 222}
    monkeypatch.setattr(delivery.store, "get_reachable_pane", lambda _tid: row)
    return calls


class _AuditLog:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def write(self, event, **kw):
        self.events.append((event, kw))

    def read(self, event, **kw):
        pass

    def error(self, event, **kw):
        self.events.append((event, kw))


def _audit(monkeypatch) -> list[tuple[str, dict]]:
    recorder = _AuditLog()
    monkeypatch.setattr(delivery, "log", recorder)
    return recorder.events


def _typed(calls, text: str) -> bool:
    return any(c[:3] == ["tmux", "send-keys", "-l"] and text in c[-1] for c in calls)


def _listen(client, session_id):
    resp = client.get("/api/bridge/listen", query_string={"session_id": session_id},
                      headers={"Authorization": f"Bearer {_TOKEN}"}, buffered=False)
    assert resp.status_code == 200
    lines = iter(resp.response)
    assert json.loads(next(lines)) == {"type": "ready", "session_id": session_id}
    return resp, lines


def test_listener_streams_only_its_own_sessions_prompts(anon_client):
    resp_a, a = _listen(anon_client, "A")
    resp_b, b = _listen(anon_client, "B")
    try:
        assert delivery.deliver("A", "hello").delivered
        assert delivery.deliver("B", "for b").delivered

        got_a = json.loads(next(a))
        got_b = json.loads(next(b))
        assert (got_a["type"], got_a["session_id"], got_a["text"]) == ("prompt", "A", "hello")
        assert (got_b["session_id"], got_b["text"]) == ("B", "for b")
    finally:
        resp_a.close()
        resp_b.close()


def test_deliver_prefers_a_connected_mod_listener(anon_client, monkeypatch):
    calls = _install_tmux(monkeypatch, capture="> hi")
    events = _audit(monkeypatch)
    resp, lines = _listen(anon_client, "A")
    try:
        res = delivery.deliver("A", "hi")
        assert res.delivered is True
        assert json.loads(next(lines))["text"] == "hi"
    finally:
        resp.close()

    assert calls == []
    assert ("bridge_delivery_outcome",
            {"trace_id": "A", "delivered": True, "detail": res.detail, "channel": "mod"}) in events


def test_deliver_falls_back_to_tmux_without_a_listener(monkeypatch):
    calls = _install_tmux(monkeypatch, capture="> hi")
    events = _audit(monkeypatch)

    res = delivery.deliver("A", "hi")

    assert res.delivered is True
    assert _typed(calls, "hi")
    assert any(c[-1] == "Enter" for c in calls)
    outcome = [kw for ev, kw in events if ev == "bridge_delivery_outcome"]
    assert outcome == [{"trace_id": "A", "delivered": True, "detail": res.detail}]


def test_listener_disconnect_unregisters_the_channel(anon_client, monkeypatch):
    calls = _install_tmux(monkeypatch, capture="> hi")
    resp, _lines = _listen(anon_client, "A")
    resp.close()

    assert mod_channel.offer("A", "hi") is False
    assert delivery.deliver("A", "hi").delivered is True
    assert _typed(calls, "hi")

    monkeypatch.setattr(delivery.store, "get_reachable_pane", lambda _tid: None)
    assert delivery.deliver("A", "hi").detail == "no reachable session"


def test_a_command_stays_on_tmux_with_a_listener_connected(anon_client, monkeypatch):
    calls = _install_tmux(monkeypatch, capture="> /exit")
    resp, _lines = _listen(anon_client, "A")
    try:
        assert delivery.deliver("A", "/exit", as_command=True).delivered is True
    finally:
        resp.close()
    assert _typed(calls, "/exit")


def test_a_prompt_offered_but_never_streamed_is_audited_lost(anon_client, monkeypatch):
    recorder = _AuditLog()
    monkeypatch.setattr(mod_channel, "log", recorder)
    resp, _lines = _listen(anon_client, "A")
    assert mod_channel.offer("A", "stranded")
    resp.close()

    lost = [kw for ev, kw in recorder.events if ev == "bridge_mod_delivery_lost"]
    assert len(lost) == 1 and lost[0]["trace_id"] == "A"
    assert len(lost[0]["frame_ids"]) == 1


def test_listen_requires_the_bridge_token(anon_client):
    assert anon_client.get("/api/bridge/listen?session_id=A").status_code == 401
    resp = anon_client.get("/api/bridge/listen", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 400


def _hooked(monkeypatch, name: str) -> threading.Event:
    fired = threading.Event()
    real = getattr(mod_channel, name)

    def _wrapped(listener):
        real(listener)
        fired.set()

    monkeypatch.setattr(mod_channel, name, _wrapped)
    return fired


def _read_line(proc, wait: float = 15.0) -> str:
    with selectors.DefaultSelector() as sel:
        sel.register(proc.stdout, selectors.EVENT_READ)
        assert sel.select(wait), "regin bridge listen printed nothing"
    return proc.stdout.readline()


def test_listen_cli_prints_prompts_and_its_exit_unregisters(tmp_db, monkeypatch):
    from web.app import create_app
    registered = _hooked(monkeypatch, "_register")
    unregistered = _hooked(monkeypatch, "_unregister")
    server = make_server("127.0.0.1", 0, create_app(), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**os.environ, "REGIN_AGENT_BRIDGE__TOKEN": _TOKEN}
    proc = subprocess.Popen(
        [sys.executable, str(_REPO / "cli" / "regin.py"), "bridge", "listen",
         "--session", "A", "--url", f"http://127.0.0.1:{server.server_port}"],
        stdout=subprocess.PIPE, text=True, env=env, cwd=_REPO)
    try:
        assert registered.wait(30), "the listen child never connected"
        assert delivery.deliver("A", "hello").delivered
        line = json.loads(_read_line(proc))
        assert line["text"] == "hello" and line["session_id"] == "A"
        assert set(line) == {"text", "id", "session_id"}

        proc.kill()
        proc.wait()
        assert unregistered.wait(15), "the server never noticed the child exit"
        assert mod_channel.offer("A", "late") is False
    finally:
        proc.kill()
        server.shutdown()


def test_send_now_goes_over_the_mod_channel(flask_client, monkeypatch):
    calls = _install_tmux(monkeypatch, capture="> hi")
    events = _audit(monkeypatch)
    resp, lines = _listen(flask_client, "A")
    try:
        res = flask_client.post("/api/sessions/A/bridge-send-now")
        frame = json.loads(next(lines))
    finally:
        resp.close()

    assert res.status_code == 200
    assert res.get_json()["delivered"] is True
    assert res.get_json()["channel"] == "mod"
    assert (frame["type"], frame["op"], frame["session_id"]) == ("control", "abort", "A")
    assert calls == []
    assert any(ev == "bridge_delivery_outcome" and kw.get("channel") == "mod"
               and kw.get("op") == "abort" for ev, kw in events)


def test_send_now_refuses_without_a_mod_listener(flask_client, monkeypatch):
    calls = _install_tmux(monkeypatch, capture="> hi")
    resp_b, _b = _listen(flask_client, "B")
    try:
        res = flask_client.post("/api/sessions/A/bridge-send-now")
    finally:
        resp_b.close()

    assert res.status_code == 409
    assert res.get_json()["delivered"] is False
    assert calls == []


def test_live_state_reports_the_mod_listener(flask_client):
    assert flask_client.get("/api/sessions/A/live-state").get_json()["mod_listener"] is False
    resp, _lines = _listen(flask_client, "A")
    try:
        assert flask_client.get("/api/sessions/A/live-state").get_json()["mod_listener"] is True
        assert flask_client.get("/api/sessions/B/live-state").get_json()["mod_listener"] is False
    finally:
        resp.close()
    assert flask_client.get("/api/sessions/A/live-state").get_json()["mod_listener"] is False


def test_listen_cli_passes_control_frames_through():
    from cli.commands.bridge import listener_lines

    stream = [json.dumps({"type": "ready", "session_id": "A"}),
              json.dumps({"type": "control", "op": "abort", "id": "1", "session_id": "A"}),
              json.dumps({"type": "prompt", "text": "hi", "id": "2", "session_id": "A"})]

    assert [json.loads(line) for line in listener_lines(stream)] == [
        {"op": "abort", "id": "1", "session_id": "A"},
        {"text": "hi", "id": "2", "session_id": "A"},
    ]


def test_send_now_respects_the_bridge_kill_switch(flask_client, monkeypatch):
    resp, _lines = _listen(flask_client, "A")
    try:
        monkeypatch.setattr(settings.agent_bridge, "enabled", False)
        res = flask_client.post("/api/sessions/A/bridge-send-now")
        state = flask_client.get("/api/sessions/A/live-state").get_json()
        assert mod_channel._listeners["A"][-1]._frames.empty()
    finally:
        resp.close()

    assert res.status_code == 409
    assert res.get_json()["detail"] == "bridge disabled"
    assert state["mod_listener"] is False
