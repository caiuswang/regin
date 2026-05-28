"""Generic provider fallback stub."""

from __future__ import annotations

import os
import re
from pathlib import Path

from lib.providers.base import AgentProvider, ProviderCapabilities


_SKILL_READ_CONTENT_RE = re.compile(r"^\.agent/skills/([^/]+)/content\.md$")


class GenericProvider(AgentProvider):
    provider_id = "generic"
    display_name = "Generic Agent"
    capabilities = ProviderCapabilities(
        skills=False,
        hooks=False,
        sessions=False,
        transcript_usage=False,
    )

    def __init__(self, overrides: dict | None = None):
        self._overrides = overrides or {}

    def _path(self, key: str, default: Path) -> Path:
        raw = self._overrides.get(key)
        if raw in (None, ""):
            return default
        return Path(os.path.expanduser(str(raw)))

    def global_skills_dir(self) -> Path:
        return self._path("skills_dir", Path.home() / ".agent" / "skills")

    def project_skills_subpath(self) -> tuple[str, ...]:
        return (".agent", "skills")

    def skill_invoke_path(self, skill_id: str) -> str:
        return f".agent/skills/{skill_id}/invoke"

    def skill_content_relpath(self, skill_id: str) -> str:
        return f".agent/skills/{skill_id}/content.md"

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
        return self._path("plans_dir", Path.home() / ".agent" / "plans")

    def traces_dir(self) -> Path:
        return self._path("traces_dir", Path.home() / ".agent" / "traces")

    def hook_settings_path(self) -> Path:
        return self._path("hook_settings_path", Path.home() / ".agent" / "settings.json")

    def hook_manager_config_path(self) -> Path:
        return self._path("hook_manager_config_path", Path.home() / ".agent" / "hook-manager-config.json")

    def hook_payload_log_path(self) -> Path:
        return self._path("hook_payload_log_path", Path.home() / ".agent" / "hook-payloads.jsonl")

    def transcript_projects_dir(self) -> Path:
        return self._path("transcript_projects_dir", Path.home() / ".agent" / "projects")
