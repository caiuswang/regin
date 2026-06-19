"""Codex provider stub.

This adapter is intentionally contract-complete but capability-limited
for the architecture-only milestone.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from lib.providers.base import AgentProvider, ProviderCapabilities
from hook_manager.core import HookPayload, HookResponse, PermissionOption, PermissionRequestInfo


_SKILL_READ_CONTENT_RE = re.compile(r"^\.codex/skills/([^/]+)/content\.md$")


class CodexProvider(AgentProvider):
    provider_id = "codex"
    display_name = "OpenAI Codex"
    capabilities = ProviderCapabilities(
        skills=True,
        hooks=True,
        sessions=True,
        transcript_usage=True,
    )
    # Codex does not emit SessionEnd in real runs, so its per-turn Stop is
    # mapped to a synthetic session-end marker (see session_lifecycle).
    synthesizes_session_end_from_stop = True

    def __init__(self, overrides: dict | None = None):
        self._overrides = overrides or {}

    def hook_events(self) -> tuple[str, ...] | None:
        # Codex currently exposes a narrower hook set than Claude's full spec.
        return (
            "SessionStart",
            "SessionEnd",
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "Stop",
        )

    def permission_request_events(self) -> tuple[str, ...]:
        return ("PreToolUse",)

    def _path(self, key: str, default: Path) -> Path:
        raw = self._overrides.get(key)
        if raw in (None, ""):
            return default
        return Path(os.path.expanduser(str(raw)))

    def global_skills_dir(self) -> Path:
        return self._path("skills_dir", Path.home() / ".codex" / "skills")

    def project_skills_subpath(self) -> tuple[str, ...]:
        return (".codex", "skills")

    def build_permission_request_info(self, payload: HookPayload) -> PermissionRequestInfo | None:
        if payload.event != "PreToolUse":
            return None
        raw = payload.raw
        if not _looks_like_permission_request(raw):
            return None
        suggestions = raw.get("permission_suggestions") or raw.get("permission_options") or []
        if not isinstance(suggestions, list):
            suggestions = []
        options = _options_from_suggestions(suggestions)
        if not options:
            options = [
                PermissionOption(
                    id="allow_once",
                    label="Allow once",
                    description="Allow this exact tool call for the current run.",
                ),
                PermissionOption(
                    id="deny",
                    label="Deny",
                    description="Deny this permission request.",
                ),
            ]
        return PermissionRequestInfo(
            tool_name=payload.tool_name,
            tool_input_summary=_summarize_tool_input(payload.tool_input),
            cwd=payload.cwd,
            permission_mode=payload.permission_mode,
            requested_permission=_requested_permission(payload.tool_name, payload.tool_input, raw),
            suggestions=suggestions,
            options=options,
            default_option_id=options[0].id,
        )

    def serialize_permission_decision(
        self,
        info: PermissionRequestInfo,
        selected_option_id: str | None = None,
    ) -> HookResponse:
        return HookResponse(
            permission_decision="ask",
            permission_reason=_format_permission_reason(info),
        )

    def skill_invoke_path(self, skill_id: str) -> str:
        return f".codex/skills/{skill_id}/invoke"

    def skill_content_relpath(self, skill_id: str) -> str:
        return f".codex/skills/{skill_id}/content.md"

    def skill_id_from_read_path(self, file_path: str, *, home: str | None = None) -> str | None:
        if not file_path:
            return None
        home = home or os.path.expanduser("~")
        if file_path.startswith(home):
            rel = file_path[len(home) + 1:]
        else:
            rel = file_path
        m = _SKILL_READ_CONTENT_RE.match(rel)
        return m.group(1) if m else None

    def plans_dir(self) -> Path:
        return self._path("plans_dir", Path.home() / ".codex" / "plans")

    def traces_dir(self) -> Path:
        return self._path("traces_dir", Path.home() / ".codex" / "traces")

    def hook_settings_path(self) -> Path:
        return self._path("hook_settings_path", Path.home() / ".codex" / "hooks.json")

    def hook_manager_config_path(self) -> Path:
        return self._path("hook_manager_config_path", Path.home() / ".codex" / "hook-manager-config.json")

    def hook_payload_log_path(self) -> Path:
        return self._path("hook_payload_log_path", Path.home() / ".codex" / "hook-payloads.jsonl")

    def transcript_projects_dir(self) -> Path:
        return self._path("transcript_projects_dir", Path.home() / ".codex" / "sessions")


def _looks_like_permission_request(raw: dict) -> bool:
    return bool(
        raw.get("permission_request")
        or raw.get("requires_permission")
        or raw.get("sandbox_permissions")
        or raw.get("permission_suggestions")
        or raw.get("permission_options")
    )


def _summarize_tool_input(tool_input: dict) -> dict:
    # Codex never carries a `pattern` arg, so drop it from the default key set.
    from lib.trace.tool_input_summary import summarize_tool_input
    return summarize_tool_input(
        tool_input, keys=("command", "description", "file_path", "path", "url"),
    )


def _requested_permission(tool_name: str | None, tool_input: dict, raw: dict) -> str:
    justification = raw.get("justification")
    if isinstance(justification, str) and justification:
        return justification[:500]
    if tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str) and command:
            return f"Run shell command: {command[:500]}"
    path = tool_input.get("file_path") or tool_input.get("path")
    if isinstance(path, str) and path:
        return f"Use {tool_name or 'tool'} on {path[:500]}"
    return f"Use tool: {tool_name or 'unknown'}"


def _options_from_suggestions(suggestions: list[dict]) -> list[PermissionOption]:
    options: list[PermissionOption] = []
    for i, suggestion in enumerate(suggestions):
        if not isinstance(suggestion, dict):
            continue
        option_id = str(suggestion.get("id") or f"option_{i + 1}")
        options.append(PermissionOption(
            id=option_id,
            label=str(suggestion.get("label") or option_id.replace("_", " ").title()),
            description=str(suggestion.get("description") or ""),
            updated_permissions=[suggestion],
        ))
    if not any(o.id == "deny" for o in options):
        options.append(PermissionOption(
            id="deny",
            label="Deny",
            description="Deny this permission request.",
        ))
    return options


def _format_permission_reason(info: PermissionRequestInfo) -> str:
    lines = [
        f"Permission requested: {info.requested_permission}",
        f"Tool: {info.tool_name or 'unknown'}",
    ]
    if info.cwd:
        lines.append(f"CWD: {info.cwd}")
    if info.tool_input_summary:
        details = ", ".join(f"{k}={v}" for k, v in info.tool_input_summary.items())
        lines.append(f"Details: {details}")
    if info.options:
        option_text = "; ".join(
            f"{option.id} ({option.label})" for option in info.options
        )
        lines.append(f"Options: {option_text}")
    if info.default_option_id:
        lines.append(f"Default option: {info.default_option_id}")
    return "\n".join(lines)
