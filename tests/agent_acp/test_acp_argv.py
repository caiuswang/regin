"""The ACP child's argv comes from the provider, not a hardcoded `kimi acp`."""

from __future__ import annotations

import shutil

import pytest

from lib.agent_acp import client as acp_client
from lib.agent_acp import launcher
from lib.agent_acp.client import AcpLaunchRefused, resolve_acp_command
from lib.providers.registry import build_provider


@pytest.fixture
def on_path(monkeypatch):
    """`shutil.which` answering only for the binaries named."""
    present: set[str] = set()
    monkeypatch.setattr(shutil, "which",
                        lambda b: f"/opt/bin/{b}" if b in present else None)
    return present


def test_acp_command_comes_from_provider(on_path):
    on_path.update({"kimi", "codebuddy"})
    kimi = build_provider("kimi")
    codebuddy = build_provider("codebuddy")

    # The argv `resolve_acp_command(None)` built before the seam existed.
    assert kimi.acp_argv() == [shutil.which("kimi"), "acp"]
    assert resolve_acp_command(None) == ["/opt/bin/kimi", "acp"]
    assert resolve_acp_command(None, kimi) == ["/opt/bin/kimi", "acp"]
    assert resolve_acp_command(None, codebuddy) == [
        "/opt/bin/codebuddy", "--acp"]
    assert resolve_acp_command(["x", "y"], codebuddy) == ["x", "y"]


@pytest.mark.parametrize("pid", ["kimi", "codebuddy"])
def test_missing_binary_refuses(on_path, pid):
    with pytest.raises(AcpLaunchRefused, match=f"{pid} CLI not found"):
        build_provider(pid).acp_argv()
    with pytest.raises(launcher.LaunchRefused, match=f"{pid} CLI not found"):
        launcher.launch_run("hi", cwd="/tmp", provider=build_provider(pid))


def test_provider_without_acp_refuses(on_path):
    with pytest.raises(AcpLaunchRefused, match="workbuddy speaks no ACP"):
        resolve_acp_command(None, build_provider("workbuddy"))


@pytest.mark.parametrize("pid", ["kimi", "codebuddy"])
def test_channel_launcher_spawns_the_providers_argv(monkeypatch, on_path, pid):
    on_path.update({"kimi", "codebuddy"})
    spawned: list[list[str]] = []

    def _popen(argv, **_kw):
        spawned.append(argv)
        raise OSError("not spawned in tests")

    monkeypatch.setattr(acp_client.subprocess, "Popen", _popen)
    provider = build_provider(pid)
    with pytest.raises(launcher.LaunchRefused):
        provider.channel_launcher()("hi", cwd="/tmp")
    assert spawned == [provider.acp_argv()]
