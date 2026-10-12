"""Held permission requests over the Mod channel (feature 158, S2).

A tmux-observed session whose regin-permit mod holds `GET /api/bridge/hold`
open for a call is decided from the web through that stream (the routing
table in the F158 spec), only by a decision naming its own
`(session_id, tool_use_id)` (J4), at most once (J4), and stops being held the
moment the stream ends (J2). Every other decision takes the tmux path exactly
as before (J3). The subprocess tests run the real `regin bridge hold` child
against a real server socket, so the exit codes and the EOF-driven ending are
exercised as shipped.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from lib.agent_bridge import delivery, mod_hold
from lib.settings import settings

_TOKEN = "mod-hold-token"
_BRIDGE = {"Authorization": f"Bearer {_TOKEN}"}
_REPO = Path(__file__).resolve().parents[2]
_REQUEST = {"tool": "Bash", "input": {"command": "rm -rf build",
                                      "description": "Clean"}}


@pytest.fixture(autouse=True)
def _bridge_on(monkeypatch):
    cfg = settings.agent_bridge
    monkeypatch.setattr(cfg, "enabled", True)
    monkeypatch.setattr(cfg, "token", _TOKEN)
    yield
    assert mod_hold._held == {}, "a test leaked a held request"


@pytest.fixture
def tmux(monkeypatch):
    """Every tmux-path decision the route makes, instead of a real pane."""
    calls: list[tuple] = []

    def _decide(trace_id, option_index):
        calls.append(("decide", trace_id, option_index))
        return delivery.DeliveryResult(True, "selected option 1 in %7")

    def _answer(trace_id, option_index, text=None, expect_chat=False):
        calls.append(("answer", trace_id, option_index))
        return delivery.DeliveryResult(True, "selected option 1 in %7")

    monkeypatch.setattr(delivery, "deliver_decision_option", _decide)
    monkeypatch.setattr(delivery, "deliver_answer", _answer)
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


def _hold(client, session_id, tool_use_id):
    resp = client.get("/api/bridge/hold", headers=_BRIDGE, buffered=False,
                      query_string={"session_id": session_id,
                                    "tool_use_id": tool_use_id})
    assert resp.status_code == 200
    lines = iter(resp.response)
    assert json.loads(next(lines))["type"] == "ready"
    return resp, lines


def _decide(client, session_id, **body):
    return client.post(f"/api/sessions/{session_id}/bridge-decide", json=body)


def _mod_held(client, session_id):
    return client.get(f"/api/sessions/{session_id}/live-state").get_json()["mod_held"]


# ── routing: the web's decision and which channel it takes ──────


def test_decisions_without_a_held_request_keep_the_tmux_path(flask_client, tmux):
    res = _decide(flask_client, "A", option_index=0, behavior="allow")

    assert res.status_code == 200
    body = res.get_json()
    assert set(body) == {"delivered", "detail", "id"}
    assert (body["delivered"], body["detail"]) == (True, "selected option 1 in %7")
    assert tmux == [("decide", "A", 0)]


def test_bridge_answer_never_uses_the_mod_channel(flask_client, tmux):
    resp, _lines = _hold(flask_client, "A", "T")
    try:
        res = flask_client.post("/api/sessions/A/bridge-answer",
                                json={"option_index": 0})
        still_held = mod_hold.is_held("A", "T")
    finally:
        resp.close()

    assert res.get_json()["delivered"] is True
    assert tmux == [("answer", "A", 0)]
    assert still_held


def test_a_decision_only_reaches_its_own_held_request(flask_client, tmux):
    resp_a, _a = _hold(flask_client, "A", "T1")
    resp_b, _b = _hold(flask_client, "B", "T2")
    try:
        res = _decide(flask_client, "A", tool_use_id="T2", behavior="allow",
                      option_index=0)
        held = (mod_hold.is_held("A", "T1"), mod_hold.is_held("B", "T2"))
        frames = [h.frame for h in mod_hold._held.values()]
    finally:
        resp_a.close()
        resp_b.close()

    assert held == (True, True)
    assert frames == [None, None]
    assert "channel" not in res.get_json()
    assert tmux == [("decide", "A", 0)]


def test_two_held_permissions_without_a_tool_use_id_are_refused(flask_client, tmux):
    resp_1, _1 = _hold(flask_client, "A", "T1")
    resp_2, _2 = _hold(flask_client, "A", "T2")
    try:
        res = _decide(flask_client, "A", behavior="allow", option_index=0)
        held = mod_hold.held_ids("A")
    finally:
        resp_1.close()
        resp_2.close()

    assert res.status_code == 409
    assert res.get_json()["error"] == "ambiguous held request"
    assert sorted(held) == ["T1", "T2"]
    assert tmux == []


def test_a_held_request_resolves_at_most_once(flask_client, tmux):
    resp, lines = _hold(flask_client, "A", "T")
    try:
        first = _decide(flask_client, "A", tool_use_id="T", behavior="deny",
                        reason="not on main")
        outcome = json.loads(next(lines))
        second = _decide(flask_client, "A", tool_use_id="T", behavior="allow",
                         option_index=0)
        rest = list(lines)
    finally:
        resp.close()

    assert first.get_json()["channel"] == "mod"
    assert outcome == {"type": "outcome", "op": "decide", "behavior": "deny",
                       "reason": "not on main"}
    assert rest == []
    assert "channel" not in second.get_json()
    assert tmux == [("decide", "A", 0)]


def test_concurrent_endings_pick_exactly_one():
    hold = mod_hold.HoldBody("A", "T", None)
    lines = iter(hold)
    assert json.loads(next(lines))["type"] == "ready"
    barrier = threading.Barrier(3)
    won: list[str] = []

    def _end(name, fn):
        barrier.wait()
        if fn():
            won.append(name)

    threads = [
        threading.Thread(target=_end, args=("allow", lambda: mod_hold.resolve("A", "T", "allow"))),
        threading.Thread(target=_end, args=("deny", lambda: mod_hold.resolve("A", "T", "deny"))),
        threading.Thread(target=_end, args=("release", lambda: mod_hold.release("A", "T"))),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    hold.close()

    assert len(won) == 1
    assert hold._hold.ending in (mod_hold.RESOLVED, mod_hold.RELEASED)


def test_a_body_never_streamed_holds_nothing():
    body = mod_hold.HoldBody("A", "T", None)
    assert not mod_hold.is_held("A", "T")
    body.close()


def test_a_stream_whose_peer_died_deregisters_without_close():
    import socket
    server_end, client_end = socket.socketpair()
    body = mod_hold.HoldBody("A", "T", server_end)
    lines = iter(body)
    next(lines)
    assert mod_hold.is_held("A", "T")
    client_end.close()

    assert list(lines) == []
    assert not mod_hold.is_held("A", "T")
    server_end.close()


def test_a_hold_that_ends_before_it_is_decided_leaves_it_to_tmux(
        flask_client, tmux, monkeypatch):
    resp, _lines = _hold(flask_client, "A", "T")
    real = mod_hold.resolve

    def _gone_first(*args, **kw):
        resp.close()
        return real(*args, **kw)

    monkeypatch.setattr(mod_hold, "resolve", _gone_first)
    res = _decide(flask_client, "A", tool_use_id="T", behavior="allow",
                  option_index=0)

    assert "channel" not in res.get_json()
    assert tmux == [("decide", "A", 0)]


def test_a_second_hold_for_the_same_call_is_refused(flask_client):
    resp, _lines = _hold(flask_client, "A", "T")
    try:
        again = flask_client.get("/api/bridge/hold", headers=_BRIDGE,
                                 query_string={"session_id": "A", "tool_use_id": "T"})
    finally:
        resp.close()
    assert again.status_code == 409


def test_a_held_decision_needs_a_behavior(flask_client, tmux):
    resp, _lines = _hold(flask_client, "A", "T")
    try:
        res = _decide(flask_client, "A", tool_use_id="T", option_index=0)
        held = mod_hold.is_held("A", "T")
    finally:
        resp.close()
    assert res.status_code == 400
    assert held and tmux == []


def test_the_bridge_kill_switch_keeps_held_requests_off_the_web(flask_client, tmux, monkeypatch):
    resp, _lines = _hold(flask_client, "A", "T")
    try:
        monkeypatch.setattr(settings.agent_bridge, "enabled", False)
        res = _decide(flask_client, "A", tool_use_id="T", behavior="allow")
        state = _mod_held(flask_client, "A")
        held = mod_hold.is_held("A", "T")
    finally:
        resp.close()
    assert res.get_json() == {"delivered": False, "detail": "bridge disabled"}
    assert state == [] and held


def test_live_state_lists_held_requests(flask_client):
    assert _mod_held(flask_client, "A") == []
    resp, _lines = _hold(flask_client, "A", "T")
    try:
        assert _mod_held(flask_client, "A") == [{"tool_use_id": "T"}]
        assert _mod_held(flask_client, "B") == []
    finally:
        resp.close()
    assert _mod_held(flask_client, "A") == []


def test_hold_and_release_require_the_bridge_token(anon_client):
    assert anon_client.get("/api/bridge/hold?session_id=A&tool_use_id=T").status_code == 401
    assert anon_client.post("/api/bridge/release", json={}).status_code == 401
    assert anon_client.get("/api/bridge/hold?session_id=A",
                           headers=_BRIDGE).status_code == 400


# ── the real `regin bridge hold` child ──────────────────────────


@pytest.fixture
def server(tmp_db):
    from web.app import create_app
    srv = make_server("127.0.0.1", 0, create_app(), threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _hooked(monkeypatch, name: str) -> threading.Event:
    fired = threading.Event()
    real = getattr(mod_hold, name)

    def _wrapped(hold):
        real(hold)
        fired.set()

    monkeypatch.setattr(mod_hold, name, _wrapped)
    return fired


def _spawn(url, verb, *, session="A", tool_use_id="T", stdin=None):
    env = {**os.environ, "REGIN_AGENT_BRIDGE__TOKEN": _TOKEN}
    proc = subprocess.Popen(
        [sys.executable, str(_REPO / "cli" / "regin.py"), "bridge", verb,
         "--session", session, "--tool-use-id", tool_use_id, "--url", url],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=_REPO)
    if stdin is not None:
        proc.stdin.write(stdin)
    proc.stdin.close()
    return proc


def test_held_permission_is_allowed_from_the_web_over_the_mod_channel(
        server, flask_client, tmux, monkeypatch):
    sends: list = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **_kw: sends.append(cmd))
    audit = _AuditLog()
    monkeypatch.setattr(mod_hold, "log", audit)
    registered = _hooked(monkeypatch, "_register")
    proc = _spawn(server, "hold", stdin=json.dumps(_REQUEST))
    try:
        assert registered.wait(30), "the hold child never connected"
        res = _decide(flask_client, "A", tool_use_id="T", behavior="allow")
        out, _err = proc.communicate(timeout=30)
    finally:
        proc.kill()

    assert res.get_json()["delivered"] is True
    assert proc.returncode == 0
    assert json.loads(out) == {"op": "decide", "behavior": "allow"}
    assert any(ev == "bridge_delivery_outcome" and kw.get("channel") == "mod"
               and kw.get("tool_use_id") == "T" for ev, kw in audit.events)
    assert sends == [] and tmux == []


def test_released_hold_exits_3(server, monkeypatch):
    registered = _hooked(monkeypatch, "_register")
    proc = _spawn(server, "hold", stdin=json.dumps(_REQUEST))
    try:
        assert registered.wait(30), "the hold child never connected"
        release = _spawn(server, "release")
        assert release.wait(30) == 0
        out, _err = proc.communicate(timeout=30)
    finally:
        proc.kill()
    assert proc.returncode == 3 and out == ""
    assert not mod_hold.is_held("A", "T")


def test_held_stream_ends_when_the_hold_process_dies(server, flask_client, tmux, monkeypatch):
    registered = _hooked(monkeypatch, "_register")
    ended = _hooked(monkeypatch, "_finish")
    proc = _spawn(server, "hold", stdin=json.dumps(_REQUEST))
    try:
        assert registered.wait(30), "the hold child never connected"
        assert _mod_held(flask_client, "A") == [{"tool_use_id": "T"}]
        proc.kill()
        proc.wait()
        assert ended.wait(15), "the server never noticed the child exit"
    finally:
        proc.kill()

    assert _mod_held(flask_client, "A") == []
    res = _decide(flask_client, "A", behavior="allow", option_index=0)
    assert "channel" not in res.get_json()
    assert tmux == [("decide", "A", 0)]


def test_unreachable_regin_exits_neither_0_nor_3():
    proc = _spawn("http://127.0.0.1:9", "hold", stdin=json.dumps(_REQUEST))
    out, _err = proc.communicate(timeout=60)
    assert proc.returncode not in (0, 3) and out == ""
