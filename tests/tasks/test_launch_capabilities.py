"""Feature #140 — a provider declares how a task run may be launched.

The vocabulary (`launch` modes, parameter fields) lives in the adapter, so
arm validation, the launch-options answer and doctor read one declaration and
shared code carries no per-provider branch.
"""

from __future__ import annotations

import shutil

import pytest

from lib import doctor
from lib.agent_sdk import client
from lib.providers import registry
from lib.providers.base import AgentProvider
from lib.providers.registry import build_provider
from lib.tasks import runs
from lib.tasks.arm import parse_arm
from lib.tasks.capture import CaptureRejected


class _SdkOnlyProvider(AgentProvider):
    """A provider with a launch channel but no parameter fields."""

    provider_id = "sdkonly"
    display_name = "Sdk Only"

    def __init__(self, overrides=None):
        self._overrides = overrides or {}

    def launch_capabilities(self):
        return {"launch_modes": ["sdk"], "fields": [], "binary": "sdkonly",
                "pane_commands": [], "labels": {"sdk": "Sdk"}}


@pytest.fixture
def sdk_only(monkeypatch):
    monkeypatch.setitem(registry._PROVIDER_BUILDERS, "sdkonly",
                        _SdkOnlyProvider)


def test_provider_declares_launch_capabilities():
    claude = build_provider("claude").launch_capabilities()
    assert claude["launch_modes"] == ["sdk", "tmux"]
    assert claude["fields"] == ["model", "permission_mode", "effort"]
    assert claude["binary"] == "claude"

    kimi = build_provider("kimi").launch_capabilities()
    assert kimi["launch_modes"] == ["sdk", "tmux"]
    assert kimi["fields"] == ["model"]
    assert kimi["binary"] == "kimi"
    assert kimi["labels"]["sdk"] == "ACP"


@pytest.mark.parametrize("provider_id", ["codex", "generic"])
def test_provider_without_channel_declares_nothing(provider_id):
    caps = build_provider(provider_id).launch_capabilities()
    assert caps["launch_modes"] == []
    assert caps["fields"] == []


@pytest.mark.parametrize("provider_id", ["codex", "generic"])
def test_arm_rejects_provider_without_launch_channel(provider_id):
    with pytest.raises(CaptureRejected) as err:
        parse_arm({"provider": provider_id})
    assert err.value.field == "provider"
    assert provider_id in str(err.value)


def test_arm_rejects_launch_mode_provider_does_not_declare(sdk_only):
    with pytest.raises(CaptureRejected) as err:
        parse_arm({"provider": "sdkonly", "launch": "tmux"})
    assert err.value.field == "launch"
    assert "sdkonly" in str(err.value) and "tmux" in str(err.value)


def test_arm_accepts_launch_mode_provider_declares():
    for mode in ("sdk", "tmux"):
        fields = parse_arm({"provider": "kimi", "launch": mode})
        assert fields["params"]["launch"] == mode


def test_arm_rejects_field_provider_does_not_declare(sdk_only):
    with pytest.raises(CaptureRejected) as err:
        parse_arm({"provider": "sdkonly", "model": "m1"})
    assert err.value.field == "model"
    assert "sdkonly" in str(err.value)


def test_claude_arm_unchanged():
    fields = parse_arm({"model": "opus", "permission_mode": "plan",
                        "effort": "high", "launch": "tmux"})
    params = fields["params"]
    assert params["provider"] == "claude"
    assert (params["model"], params["permission_mode"], params["effort"],
            params["launch"]) == ("opus", "plan", "high", "tmux")
    assert parse_arm({})["params"]["launch"] == "sdk"
    with pytest.raises(CaptureRejected) as err:
        parse_arm({"launch": "carrier-pigeon"})
    assert err.value.field == "launch"
    assert str(err.value) == "unknown launch 'carrier-pigeon'"


def test_channel_launcher_refuses_provider_without_channel():
    with pytest.raises(runs.RunRefused):
        runs._channel_launcher("codex")


def test_launch_options_lists_providers_with_availability(flask_client,
                                                          monkeypatch):
    monkeypatch.setattr(
        shutil, "which",
        lambda name, *a, **k: "/bin/kimi" if name == "kimi" else None)

    body = flask_client.get("/api/agent-runs/launch-options").get_json()

    rows = {row["id"]: row for row in body["providers"]}
    assert set(rows) == {"claude", "kimi", "codebuddy"}
    assert rows["codebuddy"]["available"] is False
    assert rows["kimi"] == {
        "id": "kimi", "display_name": "Kimi Code", "binary": "kimi",
        "available": True, "launch_modes": ["sdk", "tmux"],
        "fields": ["model"], "labels": {"sdk": "ACP", "tmux": "tmux"},
        "models": [], "default_model": ""}
    assert rows["claude"]["available"] is False
    assert rows["claude"]["launch_modes"] == ["sdk", "tmux"]
    assert rows["claude"]["binary"] == "claude"
    assert rows["claude"]["models"] == list(client.MODEL_CHOICES)


def test_doctor_reports_task_launch_providers(monkeypatch):
    monkeypatch.setattr(
        doctor, "_which",
        lambda name: "/bin/kimi" if name == "kimi" else None)
    monkeypatch.setattr(doctor, "_version", lambda cmd: " ".join(cmd) + " 9.9")

    items = {i["id"]: i for i in doctor._task_launch_provider_items()}

    assert set(items) == {"launch_claude", "launch_kimi", "launch_codebuddy"}
    assert items["launch_codebuddy"]["present"] is False
    assert items["launch_kimi"]["present"] is True
    assert items["launch_kimi"]["version"] == "kimi --version 9.9"
    assert items["launch_claude"]["present"] is False
    assert items["launch_claude"]["optional"] is True
    groups = {g["name"] for g in doctor.run_checks()["groups"]}
    assert "Task launch providers" in groups
