"""Feature #168 S4 — regin's MCP servers registered into codebuddy-family configs."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli import output as cli_output
from cli.app import app
from lib import settings as settings_mod
from lib.providers import build_provider, list_provider_ids
from lib.providers.base import ProviderCapabilities
from lib.providers.codebuddy import CodeBuddyFamily, CodeBuddyProvider
from lib.providers.mcp_registration import (
    MEMORY,
    SEND_TO_USER,
    McpConfigUnreadable,
    register_all,
    register_provider,
    server_entry,
)

USER_SERVER = {"command": "npx", "args": ["-y", "@acme/tool"], "env": {"TOKEN": "x"}}
APPROVALS = b'{\n  "abc123::custom-mcp:acme": {"approved": true}\n}\n'


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(settings_mod.settings, "providers", {})
    monkeypatch.setattr(settings_mod.settings, "codebuddy_config_dir", None)
    monkeypatch.setattr(settings_mod.settings, "workbuddy_config_dir", None)
    return tmp_path


@pytest.fixture
def cli_out(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(cli_output, "_stdout", buf)
    return buf


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _servers(path: Path) -> dict:
    return json.loads(path.read_text())["mcpServers"]


class _HooklessBuddy(CodeBuddyProvider):
    provider_id = "hookless"
    family = CodeBuddyFamily(root_dirname=".hookless", config_dir_setting="codebuddy_config_dir",
                             mcp_config_file=".mcp.json", mcp_needs_user_trust=False)
    capabilities = ProviderCapabilities(skills=True, hooks=False)


def _mcp_providers():
    built = [build_provider(pid) for pid in list_provider_ids()]
    return [p for p in built if p.mcp_config_path() is not None]


def test_mcp_registration_respects_hooks_capability(home):
    cfg = home / ".codebuddy" / ".mcp.json"
    _write(cfg, {"mcpServers": {"acme": USER_SERVER}, "other": 1})
    hookless = _HooklessBuddy()
    _write(hookless.mcp_config_path(), {"mcpServers": {
        "acme": USER_SERVER, SEND_TO_USER: server_entry(SEND_TO_USER)}})

    results = {r.provider_id: r for r in register_all(_mcp_providers() + [hookless])}

    assert set(results) == {"codebuddy", "workbuddy", "hookless"}
    servers = _servers(cfg)
    assert list(servers) == ["acme", MEMORY, SEND_TO_USER]
    assert servers["acme"] == USER_SERVER
    assert json.loads(cfg.read_text())["other"] == 1
    assert servers[MEMORY]["args"][0].endswith("lib/memory/mcp_server.py")
    assert servers[SEND_TO_USER]["args"][0].endswith("lib/agent_messages/mcp_server.py")
    assert results["hookless"].removed == [SEND_TO_USER]
    assert _servers(hookless.mcp_config_path()) == {
        "acme": USER_SERVER, MEMORY: server_entry(MEMORY)}


def test_workbuddy_mcp_registration_leaves_trust_to_the_user(home, cli_out):
    root = home / ".workbuddy"
    cfg = root / "mcp.json"
    _write(cfg, {"mcpServers": {"acme": USER_SERVER}})
    approvals = root / "mcp-approvals.json"
    approvals.write_bytes(APPROVALS)
    user_entry_bytes = json.dumps(USER_SERVER, indent=2)

    out = CliRunner().invoke(app, ["mcp", "register", "--provider", "workbuddy"])

    assert out.exit_code == 0, out.output
    servers = _servers(cfg)
    assert set(servers) == {"acme", MEMORY, SEND_TO_USER}
    assert json.dumps(servers["acme"], indent=2) == user_entry_bytes
    assert not (root / ".mcp.json").exists()
    assert approvals.read_bytes() == APPROVALS
    assert "需在 WorkBuddy 连接器中心信任 memory / send-to-user" in cli_out.getvalue()
    assert not (home / ".codebuddy").exists()


def test_repeat_registration_does_not_touch_the_file(home):
    provider = build_provider("workbuddy")
    register_provider(provider)
    cfg = provider.mcp_config_path()
    before = cfg.read_bytes()

    again = register_provider(provider)

    assert again.changed is False
    assert cfg.read_bytes() == before


def test_unreadable_config_is_refused_not_overwritten(home):
    provider = build_provider("codebuddy")
    cfg = provider.mcp_config_path()
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"mcpServers": {"acme": {},}}')

    with pytest.raises(McpConfigUnreadable):
        register_provider(provider)
    assert cfg.read_text() == '{"mcpServers": {"acme": {},}}'


def test_providers_without_mcp_config_are_skipped(home):
    assert register_provider(build_provider("claude")) is None
    assert {p.provider_id for p in _mcp_providers()} == {"codebuddy", "workbuddy"}


def test_hooks_status_names_the_workbuddy_trust_step(home, monkeypatch, cli_out):
    monkeypatch.setattr(settings_mod.settings, "experimental_providers", True)

    out = CliRunner().invoke(app, ["hooks", "status", "--provider", "workbuddy"])

    assert out.exit_code == 0, out.output
    assert "需在 WorkBuddy 连接器中心信任 memory / send-to-user" in cli_out.getvalue()


def test_unscoped_register_skips_agents_that_are_not_installed(home, cli_out):
    (home / ".workbuddy").mkdir()

    out = CliRunner().invoke(app, ["mcp", "register"])

    assert out.exit_code == 0, out.output
    assert (home / ".workbuddy" / "mcp.json").exists()
    assert not (home / ".codebuddy").exists()
