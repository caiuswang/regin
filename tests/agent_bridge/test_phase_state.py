"""Event-driven main-loop phase (feature 181, B3).

The regin-bridge mod reports turn.start / turn.step / tool.call / turn.complete
to `POST /api/bridge/phase`; while its listener is connected that report alone
decides working vs idle, and no span age is read. Without a listener the phase
falls back to the screen and span heuristics as before.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from cli.commands.bridge import parse_phase_event
from lib.agent_bridge import mod_channel, phase_state
from lib.settings import settings
from web.blueprints.trace.sessions.phase import (
    _main_phase, _mod_turn_phase, _session_phase,
)

_TOKEN = "phase-token"
_BRIDGE = {"Authorization": f"Bearer {_TOKEN}"}
_SID = "sess-phase"


@pytest.fixture(autouse=True)
def _bridge_on(monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "enabled", True)
    monkeypatch.setattr(settings.agent_bridge, "token", _TOKEN)
    yield
    phase_state.forget(_SID)


@pytest.fixture
def listening():
    listener = mod_channel.Listener(_SID)
    mod_channel._register(listener)
    yield listener
    mod_channel._unregister(listener)


def _post(client, event, turn_id=None, reason=None):
    body = {"session_id": _SID, "event": event}
    if turn_id:
        body["turn_id"] = turn_id
    if reason:
        body["reason"] = reason
    resp = client.post("/api/bridge/phase", json=body, headers=_BRIDGE)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["phase"]


def _act(age_sec, now):
    return {"last_ts": (now - timedelta(seconds=age_sec)).isoformat()}


def _main(now, act=None, ended=False):
    return _main_phase(act, now, ended, _mod_turn_phase([_SID]))


def test_run_phase_follows_turn_events(anon_client, listening):
    now = datetime.now()
    seen = []
    for event in ("turn.start", "turn.step", "tool.call"):
        _post(anon_client, event, "T")
        seen.append((phase_state.current(_SID).phase, _main(now)))
    _post(anon_client, "turn.complete", "T", "answer")
    seen.append((phase_state.current(_SID).phase, _main(now)))

    assert seen == [("thinking", "working"), ("thinking", "working"),
                    ("tool", "working"), ("idle", "idle")]
    assert phase_state.current(_SID).reason == "answer"


def test_phase_does_not_decay_by_time(anon_client, listening):
    _post(anon_client, "turn.start", "T")
    later = datetime.now() + timedelta(minutes=10)

    assert phase_state.current(_SID).phase == "thinking"
    # The last span is 10 minutes old — past both the working window and the
    # stale threshold — and the verdict is still the reported one.
    assert _main(later, _act(600, later)) == "working"
    assert _main(later, None) == "working"

    _post(anon_client, "turn.complete", "T", "answer")
    # And the converse: a span a second old does not make an idle loop busy.
    assert _main(later, _act(1, later)) == "idle"


def test_phase_falls_back_to_screen_without_mod(anon_client):
    _post(anon_client, "turn.start", "T")
    assert phase_state.current(_SID) is None
    assert _mod_turn_phase([_SID]) is None

    now = datetime.now()
    act = {None: _act(120, now)}
    busy = SimpleNamespace(busy=True)
    assert _session_phase(act, [], False, None, busy, None)[0] == "working"
    assert _session_phase(act, [], False, None, None, None)[0] == "idle"


def test_a_listener_that_leaves_takes_its_phase_with_it(anon_client):
    listener = mod_channel.Listener(_SID)
    mod_channel._register(listener)
    _post(anon_client, "turn.start", "T")
    mod_channel._unregister(listener)

    again = mod_channel.Listener(_SID)
    mod_channel._register(again)
    try:
        assert phase_state.current(_SID) is None
    finally:
        mod_channel._unregister(again)


def test_a_blocker_still_outranks_the_reported_phase(anon_client, listening):
    _post(anon_client, "tool.call", "T")
    now = datetime.now()
    act = {"last_ts": now.isoformat(), "perm_ts": now.isoformat()}
    assert _main(now, act) == "waiting-permission"
    assert _main(now, act, ended=True) == "ended"


def test_another_turns_completion_does_not_idle_the_running_one():
    state = phase_state.transition(None, "turn.start", "T2")
    assert phase_state.transition(state, "turn.complete", "T1").phase == "thinking"
    assert phase_state.transition(state, "turn.complete", "T2").phase == "idle"


def test_the_runner_verdict_still_wins(anon_client, listening):
    _post(anon_client, "turn.complete", "T")
    turn = _mod_turn_phase([_SID])
    assert _session_phase({}, [], False, "working", None, turn)[0] == "working"


def test_phase_route_validates_and_needs_the_token(anon_client):
    assert anon_client.post("/api/bridge/phase", json={}).status_code == 401
    bad = {"session_id": _SID, "event": "turn.nope"}
    assert anon_client.post("/api/bridge/phase", json=bad,
                            headers=_BRIDGE).status_code == 400


def test_mod_phase_is_ignored_with_the_bridge_off(anon_client, listening,
                                                  monkeypatch):
    _post(anon_client, "turn.start", "T")
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    assert _mod_turn_phase([_SID]) is None


def test_cli_reads_one_turn_event_from_stdin():
    assert parse_phase_event('{"event":"turn.step","turn_id":"T","x":1}') == {
        "event": "turn.step", "turn_id": "T"}
    assert parse_phase_event("not json") is None
    assert parse_phase_event('{"turn_id":"T"}') is None
