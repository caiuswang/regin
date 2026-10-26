"""D345 — a task run its report sealed can still be continued: resuming its
session lifts the seal, so the continuation takes sends instead of refusing
them, and a tmux `Stop` no longer ends it as the reported attempt."""

from __future__ import annotations

import pytest

from lib.agent_bridge import delivery
from lib.settings import settings
from lib.tasks import message_gate, run_ending
from tests.tasks.test_finish_pending_messages import (  # noqa: F401 — fixtures
    _finish, _params, _send, loop, runners, typed)
from tests.tasks.test_tmux_launch import (  # noqa: F401 — fixtures
    _armed, _attempt, _run, no_sdk, tmux)
from web.blueprints import agent_runs


@pytest.fixture
def resumes(monkeypatch):
    launched = []

    def _launch(prompt, **kwargs):
        launched.append({"prompt": prompt, **kwargs})
        return kwargs.get("trace_id") or kwargs["resume"]

    monkeypatch.setattr(settings.agent_sdk, "enabled", True)
    monkeypatch.setattr(agent_runs.supervisor, "launch", _launch)
    monkeypatch.setattr(delivery, "session_is_live", lambda _sid: False)
    return launched


def _sealed_tmux_run(client):
    run = _run(client, _armed(client, launch="tmux", one_shot=True))
    assert _finish(client, run["id"]).status_code == 200
    assert _params(run["id"]).get(run_ending.END_REQUESTED)
    return run


def test_a_refused_send_names_the_session_to_resume(flask_client, tmux,
                                                    typed):
    run = _sealed_tmux_run(flask_client)

    sent = _send(flask_client, run["trace_id"], "继续刚才的会话")

    assert sent["reason"] == "run_sealed"
    assert sent["resume"]["session_id"] == run["trace_id"]


def test_resuming_a_sealed_run_lets_the_next_send_through(
        flask_client, tmux, typed, resumes):
    run = _sealed_tmux_run(flask_client)

    resp = flask_client.post("/api/agent-runs", json={
        "resume": run["trace_id"], "prompt": "继续刚才的会话"})

    assert resp.get_json()["launched"] is True, resp.get_json()
    assert resumes[0]["prompt"] == "继续刚才的会话"
    assert not _params(run["id"]).get(run_ending.END_REQUESTED)
    assert _attempt(run["id"]).status == "done", "the report stays settled"
    assert run_ending.end_reported_tmux(run["trace_id"]) is False

    sent = _send(flask_client, run["trace_id"], "再补一句")

    assert sent["delivered"] is True and "reason" not in sent
    assert typed == ["再补一句"]


def test_a_refused_resume_of_a_live_session_keeps_the_seal(
        flask_client, tmux, typed, resumes, monkeypatch):
    run = _sealed_tmux_run(flask_client)
    monkeypatch.setattr(delivery, "session_is_live", lambda _sid: True)

    resp = flask_client.post("/api/agent-runs",
                             json={"resume": run["trace_id"], "prompt": "x"})

    assert resp.status_code == 400
    assert _params(run["id"]).get(run_ending.END_REQUESTED)


def test_release_ignores_unsealed_and_unknown_ids(flask_client, tmux):
    run = _run(flask_client, _armed(flask_client, launch="tmux",
                                    one_shot=True))

    assert message_gate.release_for_resume([run["trace_id"], "nope", ""]) == []
