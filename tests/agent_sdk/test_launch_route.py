"""The launch surface (`web/blueprints/agent_runs.py`).

The route's job is to start the runner *in this process*, because the parked
question and the route that answers it must share a registry. These tests pin
the refusal behaviour and the in-process contract; `supervisor.launch` is
stubbed so no test spawns a real agent.
"""

from __future__ import annotations

import uuid

import pytest

from lib.agent_sdk import registry, store, supervisor
from lib.settings import settings


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", True)


@pytest.fixture
def stub_launch(monkeypatch):
    seen = {}

    def _launch(prompt, **kwargs):
        seen["prompt"] = prompt
        seen.update(kwargs)
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


def test_a_promptless_launch_starts_a_waiting_session(flask_client, enabled,
                                                      stub_launch):
    """Opening the session is itself the act, the way bare `claude` is at a
    terminal — the run comes up on a card with a composer for the first turn."""
    assert flask_client.post("/api/agent-runs",
                             json={"prompt": "   "}).status_code == 200
    assert flask_client.post("/api/agent-runs", json={}).status_code == 200
    assert stub_launch["prompt"] == ""


def test_a_promptless_one_shot_is_rejected(flask_client, enabled, stub_launch):
    """It would connect and immediately disconnect: the turn it was told to end
    with was never given."""
    res = flask_client.post("/api/agent-runs", json={"one_shot": True})

    assert res.status_code == 400
    assert "prompt" not in stub_launch


def test_prompt_is_bounded(flask_client, enabled, stub_launch):
    flask_client.post("/api/agent-runs", json={"prompt": "x" * 20000})

    assert len(stub_launch["prompt"]) == 8000


def test_capacity_refusal_is_surfaced(flask_client, enabled, monkeypatch):
    def _busy(prompt, **kwargs):
        raise supervisor.LaunchRefused("max_concurrent_runs reached")

    monkeypatch.setattr(supervisor, "launch", _busy)

    body = flask_client.post("/api/agent-runs", json={"prompt": "hi"}).get_json()

    assert body == {"launched": False, "detail": "max_concurrent_runs reached"}


def test_an_id_already_claimed_by_another_launch_is_a_structured_refusal(
        flask_client, enabled, monkeypatch):
    """The narrow-window twin of `_resume_target`'s "that run is still live":
    the id was taken by a launch already under way. An operator gets a
    sentence in the sheet, not a 500."""
    class _FixedUuid:
        @staticmethod
        def uuid4():
            return uuid.UUID(int=0xC0FFEE)

    monkeypatch.setattr(supervisor, "uuid", _FixedUuid)
    minted = f"sdk-{uuid.UUID(int=0xC0FFEE).hex[:12]}"
    registry.reserve_run(minted)
    try:
        res = flask_client.post("/api/agent-runs", json={"prompt": "hi"})
    finally:
        registry.release_run(minted)

    assert res.status_code == 200
    assert res.get_json() == {"launched": False,
                              "detail": "that run is already starting"}


def test_missing_sdk_package_explains_the_extra(flask_client, enabled,
                                                monkeypatch):
    def _missing(prompt, **kwargs):
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


# ── per-run overrides ────────────────────────────────────────────────

def test_per_run_overrides_reach_the_supervisor(flask_client, enabled,
                                                stub_launch):
    res = flask_client.post("/api/agent-runs", json={
        "prompt": "go", "cwd": "/tmp/x", "model": "claude-opus-5",
        "permission_mode": "plan", "one_shot": True, "resume": "abc-123",
    })

    assert res.status_code == 200
    assert stub_launch["cwd"] == "/tmp/x"
    assert stub_launch["model"] == "claude-opus-5"
    assert stub_launch["permission_mode"] == "plan"
    assert stub_launch["one_shot"] is True
    assert stub_launch["resume"] == "abc-123"


def test_unknown_permission_mode_is_a_400_not_a_dead_run(flask_client, enabled,
                                                        stub_launch):
    res = flask_client.post("/api/agent-runs",
                            json={"prompt": "go", "permission_mode": "yolo"})

    assert res.status_code == 400
    # Refused before launching: an unknown mode reaching the SDK would surface
    # as a run that died on start.
    assert "prompt" not in stub_launch


def test_resuming_a_run_regin_never_recorded_is_refused(flask_client, enabled,
                                                        stub_launch):
    """`sdk-…` is regin's name for a run, not a session the CLI can reopen — so
    with no row naming the child behind it there is nothing to resume. Reviving
    a run regin DOES have a row for is `test_resume_run.py`."""
    res = flask_client.post("/api/agent-runs",
                            json={"prompt": "go", "resume": "sdk-abc123"})

    assert res.status_code == 400
    assert "prompt" not in stub_launch


def test_omitted_overrides_stay_empty(flask_client, enabled, stub_launch):
    flask_client.post("/api/agent-runs", json={"prompt": "go"})

    assert stub_launch["cwd"] is None
    assert stub_launch["resume"] is None
    assert stub_launch["model"] == ""
    assert stub_launch["permission_mode"] == ""
    assert stub_launch["one_shot"] is False


def test_a_mode_that_shadows_gating_is_reported_on_the_launch(
        flask_client, enabled, stub_launch, monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "gate_plan", True)

    body = flask_client.post("/api/agent-runs", json={
        "prompt": "go", "permission_mode": "acceptEdits",
    }).get_json()

    assert body["launched"] is True
    assert "acceptEdits" in body["warning"]


def test_no_warning_when_nothing_is_gated(flask_client, enabled, stub_launch,
                                          monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "gate_plan", False)
    monkeypatch.setattr(settings.agent_sdk, "gated_tools", [])

    body = flask_client.post("/api/agent-runs", json={
        "prompt": "go", "permission_mode": "acceptEdits",
    }).get_json()

    assert body == {"launched": True, "trace_id": "sdk-deadbeef"}


# ── launch options ───────────────────────────────────────────────────

def test_launch_options_describe_this_install(flask_client, enabled,
                                              monkeypatch):
    from pathlib import Path
    # Real `repo_paths` are `Path`s — serialize them, or the route 500s on
    # every install that has registered a repo.
    monkeypatch.setattr(settings, "repo_paths", [Path("/repo/a"),
                                                Path("/repo/b")])
    monkeypatch.setattr(settings.agent_sdk, "permission_mode", "plan")

    body = flask_client.get("/api/agent-runs/launch-options").get_json()

    assert body["enabled"] is True
    assert body["cwds"] == ["/repo/a", "/repo/b"]
    assert body["default_permission_mode"] == "plan"
    assert "bypassPermissions" in body["permission_modes"]


def test_launch_options_report_a_disabled_tier(flask_client, monkeypatch):
    monkeypatch.setattr(settings.agent_sdk, "enabled", False)

    assert flask_client.get(
        "/api/agent-runs/launch-options").get_json()["enabled"] is False
