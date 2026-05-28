"""Provider contracts for agent-specific integration points.

regin currently defaults to Claude paths/schemas, but the core should
resolve those via a provider adapter so Codex/other agents can plug in
without hard-coded path rewrites across the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hook_manager.core import HookPayload, HookResponse, PermissionRequestInfo


@dataclass(frozen=True)
class ProviderCapabilities:
    """Feature support matrix surfaced to CLI/UI callers."""

    skills: bool = False
    hooks: bool = False
    sessions: bool = False
    transcript_usage: bool = False


class AgentProvider:
    """Base adapter for provider-specific paths and conventions."""

    provider_id: str = "unknown"
    display_name: str = "Unknown"
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def hook_events(self) -> tuple[str, ...] | None:
        """Supported hook event names for install wiring.

        Returns None to indicate "use the full spec registry".
        """
        return None

    def permission_request_events(self) -> tuple[str, ...]:
        """Events that can carry permission request details for this provider."""
        return ("PermissionRequest",)

    def build_permission_request_info(self, payload: "HookPayload") -> "PermissionRequestInfo | None":
        """Normalize provider-specific permission request payloads.

        Providers that do not support a permission prompt on the incoming event
        should return None.
        """
        return None

    def serialize_permission_decision(
        self,
        info: "PermissionRequestInfo",
        selected_option_id: str | None = None,
    ) -> "HookResponse":
        """Convert a selected permission option into provider hook output."""
        from hook_manager.core import HookResponse
        return HookResponse()

    def global_skills_dir(self) -> Path:
        raise NotImplementedError

    def project_skills_subpath(self) -> tuple[str, ...]:
        """Relative subpath used for per-project deployment targets."""
        return (".claude", "skills")

    def skill_invoke_path(self, skill_id: str) -> str:
        """Synthetic trace path for explicit slash-command invocation."""
        raise NotImplementedError

    def skill_launch_path(self, skill_id: str) -> str:
        """Synthetic trace path for assistant-initiated Skill tool launches."""
        raise NotImplementedError

    def skill_content_relpath(self, skill_id: str) -> str:
        """Relative path under $HOME for deployed skill content.md."""
        raise NotImplementedError

    def skill_id_from_read_path(self, file_path: str, *, home: str | None = None) -> str | None:
        """Return skill id when the read path points to content.md."""
        raise NotImplementedError

    def plans_dir(self) -> Path:
        raise NotImplementedError

    def traces_dir(self) -> Path:
        raise NotImplementedError

    def hook_settings_path(self) -> Path:
        raise NotImplementedError

    def hook_manager_config_path(self) -> Path:
        raise NotImplementedError

    def hook_payload_log_path(self) -> Path:
        raise NotImplementedError

    def transcript_projects_dir(self) -> Path:
        raise NotImplementedError
