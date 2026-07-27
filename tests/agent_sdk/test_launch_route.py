"""The launch surface (`web/blueprints/agent_runs.py`).

The route's job is to start the runner *in this process*, because the parked
question and the route that answers it must share a registry. These tests pin
the refusal behaviour and the in-process contract; `supervisor.launch` is
stubbed so no test spawns a real agent.
"""

from __future__ import annotations

import pytest

from lib.agent_sdk import registry, store, supervisor
from lib.settings import settings


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)


@pytest.fixture
def stub_launch(monkeypatch):
    seen = {}

    def _launch(prompt, *, cwd=None):
        seen["prompt"] = prompt
        seen["cwd"] = cwd
        return "sdk-deadbeef"

    monkeypatch.setattr(supervisor, "launch", _launch)
    return seen


def test_launch_returns_a_trace_id(flask_client, enabled, stub_launch):
    res = flask_client.post("/api/agent-runs", json={"prompt": "do a thing"})

    assert res.status_code == 200
    assert res.get_json() == {"launched": True, "trace_id": "sdk-deadbeef"}
    assert stub_launch["prompt"] == "do a thing"


def test_disabled_tier_is_a_structured_refusal_not_a_404(flask_client,
                                                         monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", False)

    res = flask_client.post("/api/agent-runs", json={"prompt": "hi"})

    assert res.status_code == 200
    assert res.get_json() == {"launched": False, "detail": "agent_sdk disabled"}


def test_empty_prompt_is_rejected(flask_client, enabled, stub_launch):
    assert flask_client.post("/api/agent-runs",
                             json={"prompt": "   "}).status_code == 400
    assert flask_client.post("/api/agent-runs", json={}).status_code == 400


def test_prompt_is_bounded(flask_client, enabled, stub_launch):
    flask_client.post("/api/agent-runs", json={"prompt": "x" * 20000})

    assert len(stub_launch["prompt"]) == 8000


def test_capacity_refusal_is_surfaced(flask_client, enabled, monkeypatch):
    def _busy(prompt, *, cwd=None):
        raise supervisor.LaunchRefused("max_concurrent_runs reached")

    monkeypatch.setattr(supervisor, "launch", _busy)

    body = flask_client.post("/api/agent-runs", json={"prompt": "hi"}).get_json()

    assert body == {"launched": False, "detail": "max_concurrent_runs reached"}


def test_missing_sdk_package_explains_the_extra(flask_client, enabled,
                                                monkeypatch):
    def _missing(prompt, *, cwd=None):
        raise ImportError("no module named claude_agent_sdk")

    monkeypatch.setattr(supervisor, "launch", _missing)

    body = flask_client.post("/api/agent-runs", json={"prompt": "hi"}).get_json()

    assert body["launched"] is False
    assert "agent-sdk" in body["detail"]


def test_status_reports_reachability_separately_from_stored_status(flask_client):
    store.upsert_run("sdk-status-test", status="running")
    try:
        body = flask_client.get("/api/agent-runs/sdk-status-test").get_json()

        assert body["status"] == "running"
        # No live runner in this process, so the row's claim is not reachability.
        assert body["owned"] is False

        registry.register_run("sdk-status-test", object())
        assert flask_client.get(
            "/api/agent-runs/sdk-status-test").get_json()["owned"] is True
    finally:
        registry.unregister_run("sdk-status-test")


def test_unknown_run_is_404(flask_client):
    assert flask_client.get("/api/agent-runs/nope").status_code == 404
