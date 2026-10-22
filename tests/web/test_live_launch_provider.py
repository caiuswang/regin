"""Feature #140 S6 — /live launch and Resume follow the session's provider.

`POST /api/agent-runs` used to know one CLI: every launch went to the Claude
SDK, and a Resume handed any id to `claude --resume` — so with
`active_provider=kimi` a kimi session was "resumed" by a CLI that has never
heard of it. A launch now names its provider, a channel provider (kimi) runs
over its ACP channel, and a Resume runs on the CLI that recorded the session,
taking it over with `session/load` under its own id (Invariant 2).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from sqlmodel import text

from lib.agent_acp import registry as acp_registry
from lib.agent_acp.launcher import launch_run
from lib.agent_sdk import resumable, supervisor
from lib.orm import SessionLocal
from lib.providers import kimi as kimi_mod
from lib.providers.claude import ClaudeProvider
from lib.providers.codebuddy import CodeBuddyProvider
from lib.providers.kimi import KimiProvider
from lib.settings import settings

FAKE = str(Path(__file__).parents[1] / "agent_acp" / "fake_acp_server.py")
_KIMI_SESSION = "session_kimi0001"
_CLAUDE_SESSION = "11111111-2222-3333-4444-555555555555"


def _until(predicate, what, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {what}")


def _requests(record: Path, method: str) -> list[dict]:
    if not record.exists():
        return []
    rows = [json.loads(line) for line in record.read_text().splitlines()]
    return [r for r in rows if r.get("method") == method]


@pytest.fixture(autouse=True)
def _close_live_runs():
    yield
    for handle in list(acp_registry._handles.values()):
        handle.close()
    # Every test reuses one session id; a closed one would refuse the next
    # test's steer as "no longer running".
    acp_registry._retired.clear()


@pytest.fixture
def enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(kimi_mod, "_WORKSPACE_TRUST_DIR",
                        tmp_path / "workspace-trust")


@pytest.fixture
def no_claude(monkeypatch):
    """Any launch reaching the Claude SDK is the bug under test."""
    seen = {}

    def _launch(prompt, **kwargs):
        seen.update(kwargs, prompt=prompt)
        return "sdk-claude01"

    monkeypatch.setattr(supervisor, "launch", _launch)
    return seen


@pytest.fixture
def channel(monkeypatch, tmp_path):
    """Kimi's channel launcher, run for real over the fake ACP server.
    → `(record path, env knobs, the kwargs each launch was handed)`."""
    record = tmp_path / "acp-requests.jsonl"
    knobs: dict = {}
    calls: list[dict] = []

    def _runner(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return launch_run(prompt, cmd=[sys.executable, FAKE],
                          env={"FAKE_ACP_RECORD": str(record), **knobs},
                          **kwargs)

    monkeypatch.setattr(KimiProvider, "channel_launcher", lambda self: _runner)
    return record, knobs, calls


def _session(trace_id: str, agent_type: str | None, *, ended=True,
             cwd: str = "") -> str:
    with SessionLocal() as session:
        session.exec(text(
            "INSERT INTO sessions (trace_id, title, started_at, last_seen,"
            " agent_type, status, ended_at, cwd)"
            " VALUES (:t, 'old', '2026-10-01 10:00:00', '2026-10-01 10:05:00',"
            " :a, :s, :e, :c)"
        ).bindparams(t=trace_id, a=agent_type, s="ended" if ended else None,
                     e="2026-10-01 10:05:00" if ended else None, c=cwd))
        session.commit()
    return trace_id


def _ended_at(trace_id: str):
    with SessionLocal() as session:
        return session.exec(text(
            "SELECT ended_at FROM sessions WHERE trace_id = :t"
        ).bindparams(t=trace_id)).first()[0]


def test_live_launch_routes_kimi_to_acp_channel(flask_client, enabled,
                                                no_claude, channel, tmp_path):
    record, _knobs, _calls = channel

    res = flask_client.post("/api/agent-runs", json={
        "provider": "kimi", "prompt": "hello", "cwd": str(tmp_path),
        "model": "kimi-k3"})

    body = res.get_json()
    assert res.status_code == 200, body
    assert body["launched"] is True and body["provider"] == "kimi"
    trace_id = body["trace_id"]
    # The real session id from session/new — no `sdk-` placeholder.
    assert trace_id.startswith("session_fake")
    assert no_claude == {}
    assert acp_registry.is_owned(trace_id)
    (new,) = _requests(record, "session/new")
    assert new["params"]["cwd"] == str(tmp_path)
    assert _requests(record, "session/set_model")[0]["params"]["modelId"] == "kimi-k3"
    _until(lambda: _requests(record, "session/prompt"), "the launch turn")
    # D8: the directory was trusted before the session opened in it.
    assert list((tmp_path / "workspace-trust").iterdir())


@pytest.mark.parametrize("body, needle", [
    ({"provider": "kimi", "permission_mode": "plan"}, "permission_mode"),
    ({"provider": "kimi", "effort": "high"}, "effort"),
    ({"provider": "nope"}, "unknown provider"),
    ({"provider": "codex"}, "no launch channel"),
    ({"provider": "kimi", "kind": "terminal"}, "terminal"),
])
def test_live_launch_refuses_what_the_provider_does_not_declare(
        flask_client, enabled, no_claude, channel, monkeypatch, body, needle):
    monkeypatch.setattr("web.blueprints.agent_runs.terminal_launch."
                        "unavailable_reason", lambda _role: "")
    res = flask_client.post("/api/agent-runs", json=body)

    assert res.status_code == 400
    assert needle in res.get_json()["error"]
    assert no_claude == {} and channel[2] == []


def test_live_launch_hands_codebuddy_its_armed_mode(flask_client, enabled,
                                                    no_claude, monkeypatch,
                                                    tmp_path):
    """D308: the sheet's model and tier reach the codebuddy session — the
    launch route used to drop the mode before the channel ever saw it."""
    record = tmp_path / "acp.jsonl"

    def _runner(prompt, **kwargs):
        return launch_run(prompt, cmd=[sys.executable, FAKE],
                          env={"FAKE_ACP_RECORD": str(record)}, **kwargs)

    monkeypatch.setattr(CodeBuddyProvider, "channel_launcher",
                        lambda self: _runner)
    res = flask_client.post("/api/agent-runs", json={
        "provider": "codebuddy", "prompt": "hello", "cwd": str(tmp_path),
        "model": "hy3", "permission_mode": "plan"})

    body = res.get_json()
    assert res.status_code == 200, body
    assert body["provider"] == "codebuddy"
    assert no_claude == {}
    assert _requests(record, "session/set_mode")[0]["params"]["modeId"] == "plan"
    assert _requests(record, "session/set_model")[0]["params"]["modelId"] == "hy3"


@pytest.fixture
def recorded(monkeypatch):
    """One kimi and one claude session, each with a transcript only its own
    CLI can find — so a lookup through the wrong provider says no."""
    _session(_KIMI_SESSION, "kimi")
    _session(_CLAUDE_SESSION, None)
    monkeypatch.setattr(KimiProvider, "find_session_transcript",
                        lambda self, sid: "/k/wire.jsonl"
                        if sid == _KIMI_SESSION else None)
    monkeypatch.setattr(ClaudeProvider, "find_session_transcript",
                        lambda self, sid: "/c/x.jsonl"
                        if sid == _CLAUDE_SESSION else None)
    monkeypatch.setattr(ClaudeProvider, "live_session_ids",
                        lambda self: frozenset())


def test_resume_uses_session_provider_not_active_provider(
        flask_client, enabled, no_claude, channel, recorded, monkeypatch):
    monkeypatch.setattr(settings, "active_provider", "claude")

    assert resumable.resume_refusal(_KIMI_SESSION) == ""
    listed = {r["session_id"]: r["provider"]
              for r in resumable.list_resumable()}
    assert listed == {_KIMI_SESSION: "kimi", _CLAUDE_SESSION: "claude"}
    res = flask_client.post("/api/agent-runs", json={"resume": _KIMI_SESSION})
    assert res.get_json()["trace_id"] == _KIMI_SESSION
    assert channel[2][0]["resume"] == _KIMI_SESSION
    assert no_claude == {}


def test_claude_session_resumes_on_claude_when_kimi_is_active(
        flask_client, enabled, no_claude, channel, recorded, monkeypatch):
    monkeypatch.setattr(settings, "active_provider", "kimi")

    assert resumable.resume_refusal(_CLAUDE_SESSION) == ""
    res = flask_client.post("/api/agent-runs", json={"resume": _CLAUDE_SESSION})
    assert res.get_json() == {"launched": True, "trace_id": "sdk-claude01"}
    assert no_claude["resume"] == _CLAUDE_SESSION
    assert channel[2] == []


def test_resume_naming_another_provider_is_refused(flask_client, enabled,
                                                   no_claude, recorded):
    res = flask_client.post("/api/agent-runs", json={
        "resume": _CLAUDE_SESSION, "provider": "kimi"})

    assert res.status_code == 400
    assert "recorded by claude" in res.get_json()["error"]
    assert no_claude == {}


def test_kimi_resume_loads_session_over_acp(flask_client, enabled, no_claude,
                                            channel, tmp_path):
    record, _knobs, _calls = channel
    _session(_KIMI_SESSION, "kimi", cwd=str(tmp_path))

    res = flask_client.post("/api/agent-runs", json={
        "resume": _KIMI_SESSION, "cwd": str(tmp_path)})

    body = res.get_json()
    assert body["launched"] is True, body
    assert body["trace_id"] == _KIMI_SESSION
    (load,) = _requests(record, "session/load")
    assert load["params"]["sessionId"] == _KIMI_SESSION
    assert load["params"]["cwd"] == str(tmp_path)
    assert _requests(record, "session/new") == []
    assert acp_registry.is_owned(_KIMI_SESSION)
    # Revived before the route answered, so the card's live poll re-arms.
    assert _ended_at(_KIMI_SESSION) is None


def test_resumed_kimi_session_waits_for_its_first_steer(
        flask_client, enabled, no_claude, channel):
    record, _knobs, _calls = channel
    _session(_KIMI_SESSION, "kimi")
    flask_client.post("/api/agent-runs", json={"resume": _KIMI_SESSION})

    # A blank prompt reopens the session without inventing a turn.
    time.sleep(0.3)
    assert _requests(record, "session/prompt") == []
    queued, _ = supervisor.send_prompt(_KIMI_SESSION, "carry on")
    assert queued
    turn = _until(lambda: _requests(record, "session/prompt"), "the steer")
    assert turn[0]["params"]["sessionId"] == _KIMI_SESSION

    again = flask_client.post("/api/agent-runs", json={"resume": _KIMI_SESSION})
    assert again.status_code == 400
    assert "still live" in again.get_json()["error"]


def test_kimi_resume_refused_without_load_session(flask_client, enabled,
                                                  no_claude, channel):
    _record, knobs, _calls = channel
    knobs["FAKE_ACP_NO_LOAD"] = "1"
    _session(_KIMI_SESSION, "kimi")

    body = flask_client.post("/api/agent-runs",
                             json={"resume": _KIMI_SESSION}).get_json()

    assert body["launched"] is False
    assert "loadSession" in body["detail"]
    assert not acp_registry.is_owned(_KIMI_SESSION)
    # The optimistic revive is put back: the session never came up.
    assert _ended_at(_KIMI_SESSION) is not None


def test_interrupted_resume_ends_its_session_on_close(
        flask_client, enabled, no_claude, channel):
    """An interrupt settles the handle while the session stays open; the
    session must not read ended until the close, which ends it with its own
    reason rather than restoring the stale pre-resume marker."""
    _record, knobs, _calls = channel
    knobs["FAKE_ACP_STOP_REASON"] = "cancelled"
    _session(_KIMI_SESSION, "kimi")
    flask_client.post("/api/agent-runs", json={
        "resume": _KIMI_SESSION, "prompt": "go"})
    handle = acp_registry.get(_KIMI_SESSION)
    _until(handle.done, "the interrupted turn to settle the handle")

    assert acp_registry.is_owned(_KIMI_SESSION)
    assert _ended_at(_KIMI_SESSION) is None
    handle.close()
    with SessionLocal() as session:
        reason = session.exec(text(
            "SELECT ended_reason FROM sessions WHERE trace_id = :t"
        ).bindparams(t=_KIMI_SESSION)).first()[0]
    assert _ended_at(_KIMI_SESSION) is not None
    assert reason == "closed"


def test_steer_sent_during_resume_handshake_runs_after_load(
        flask_client, enabled, no_claude, monkeypatch, tmp_path):
    """A send while the session is still loading is parked under the
    reservation; handing over to the channel must run it, not drop it."""
    record = tmp_path / "acp-requests.jsonl"

    def _runner(prompt, **kwargs):
        assert supervisor.send_prompt(_KIMI_SESSION, "early steer")[0]
        return launch_run(prompt, cmd=[sys.executable, FAKE],
                          env={"FAKE_ACP_RECORD": str(record)}, **kwargs)

    monkeypatch.setattr(KimiProvider, "channel_launcher", lambda self: _runner)
    _session(_KIMI_SESSION, "kimi")
    # The usual Resume: this process ran the session before and closed it.
    acp_registry._retired.add(_KIMI_SESSION)
    flask_client.post("/api/agent-runs", json={"resume": _KIMI_SESSION})

    turn = _until(lambda: _requests(record, "session/prompt"), "the steer")
    assert turn[0]["params"]["prompt"][0]["text"] == "early steer"


_WORKBUDDY_SESSION = "41ebef82-0c6e-4a0e-96be-cb68188420ce"


def test_workbuddy_session_is_not_offered_resume(flask_client, enabled,
                                                 monkeypatch):
    """D304 follow-up: WorkBuddy's desktop app owns its sessions and regin
    cannot launch it, yet the page said "no longer running — Resume" and the
    button posted a launch the route refuses ("no launch channel")."""
    from lib.providers.codebuddy import WorkBuddyProvider
    from web.blueprints.trace.sessions.map_view import _bridge_reachability

    _session(_WORKBUDDY_SESSION, "workbuddy", ended=False)
    monkeypatch.setattr(WorkBuddyProvider, "find_session_transcript",
                        lambda self, sid: "/w/x.jsonl")

    assert resumable.resume_refusal(_WORKBUDDY_SESSION) == \
        resumable.REFUSED_NOT_LAUNCHABLE
    assert _WORKBUDDY_SESSION not in {
        r["session_id"] for r in resumable.list_resumable()}
    state = _bridge_reachability(_WORKBUDDY_SESSION)
    assert (state["resumable"], state["driven_by"]) == (False, "WorkBuddy")
    res = flask_client.post("/api/agent-runs",
                            json={"resume": _WORKBUDDY_SESSION})
    assert res.status_code == 400


def test_resumable_session_names_no_outside_driver(flask_client, enabled,
                                                   recorded):
    from web.blueprints.trace.sessions.map_view import _bridge_reachability

    state = _bridge_reachability(_KIMI_SESSION)
    assert (state["resumable"], state["driven_by"]) == (True, None)
