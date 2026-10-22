"""Register regin's MCP servers into each provider's own MCP config.

regin owns exactly the keys in `REGIN_SERVERS` under `mcpServers`; every other
entry, and every other top-level key, is carried through untouched. A provider
whose `mcp_config_path()` is None gets nothing written.

`regin` is the one server carrying `send_to_user` and the pipeline tools. Where
hooks do not run it is launched with `--no-send-to-user`: that tool merely
acks, and the message is persisted by the PostToolUse hook, so without hooks
the agent would be told "delivered" for a message that never lands.
`send-to-user` is the key regin wrote before the two were one server; it stays
regin-owned so a re-register removes it. `topics` is not offered to other
providers.

WorkBuddy gates each entry on a trust approval the user grants in its
connector center (`mcp-approvals.json`, keyed by a hash of the entry's
config). regin never writes that file — it leaves entries at `written`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from lib import hooks_wiring
from lib.providers.base import AgentProvider
from lib.settings import settings
from lib.venv_paths import venv_python

MEMORY = "memory"
REGIN = "regin"
SEND_TO_USER = "send-to-user"
REGIN_SERVERS = (MEMORY, REGIN, SEND_TO_USER)

_SERVER_SCRIPTS = {
    MEMORY: ("lib", "memory", "mcp_server.py"),
    REGIN: ("lib", "regin_mcp", "server.py"),
}
NO_SEND_TO_USER = "--no-send-to-user"


class McpConfigUnreadable(Exception):
    """The provider's MCP config has content that does not parse as a JSON object."""


@dataclass
class McpRegistration:
    provider_id: str
    path: Path
    registered: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: bool = False
    trust_hint: str | None = None


def _posix(path: str) -> str:
    return path.replace(os.sep, "/")


def server_entry(name: str, *, hooks: bool = True) -> dict:
    """The same stdio launch regin's own Claude registration uses."""
    root = str(settings.project_root)
    script = os.path.join(root, *_SERVER_SCRIPTS[name])
    args = [_posix(script)]
    if name == REGIN and not hooks:
        args.append(NO_SEND_TO_USER)
    return {
        "type": "stdio",
        "command": _posix(venv_python(root)),
        "args": args,
        "env": {},
    }


def wanted_servers(provider: AgentProvider) -> tuple[str, ...]:
    if provider.mcp_config_path() is None:
        return ()
    return (MEMORY, REGIN)


def trust_hint(provider: AgentProvider) -> str | None:
    if not provider.mcp_needs_user_trust:
        return None
    names = " / ".join(wanted_servers(provider))
    return f"需在 {provider.display_name} 连接器中心信任 {names}"


def _read_config(path: Path) -> dict:
    if hooks_wiring.json_settings_unreadable(str(path)):
        raise McpConfigUnreadable(f"{path} is not valid JSON — repair it by hand first")
    data = hooks_wiring.read_json_settings(str(path))
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        raise McpConfigUnreadable(f"{path} has no mcpServers object regin can merge into")
    return data


def _merge(servers: dict, wanted: tuple[str, ...], hooks: bool,
           result: McpRegistration) -> dict:
    merged = {k: v for k, v in servers.items() if k not in REGIN_SERVERS or k in wanted}
    result.removed = [k for k in REGIN_SERVERS if k in servers and k not in wanted]
    for name in wanted:
        merged[name] = server_entry(name, hooks=hooks)
    result.registered = list(wanted)
    return merged


def register_provider(provider: AgentProvider) -> McpRegistration | None:
    """Merge regin's servers into one provider's config; None when it takes none.

    The file is only rewritten when regin's keys actually change, so a repeat
    run leaves it — and the trust hash WorkBuddy keyed on it — alone.
    """
    path = provider.mcp_config_path()
    if path is None:
        return None
    result = McpRegistration(provider.provider_id, path, trust_hint=trust_hint(provider))
    data = _read_config(path)
    servers = data.get("mcpServers", {})
    merged = _merge(servers, wanted_servers(provider),
                    provider.capabilities.hooks, result)
    if merged != servers or not path.exists():
        hooks_wiring.write_json_settings({**data, "mcpServers": merged}, str(path))
        result.changed = True
    return result


def register_all(providers: list[AgentProvider]) -> list[McpRegistration]:
    results = (register_provider(p) for p in providers)
    return [r for r in results if r is not None]
