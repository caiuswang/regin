"""CodeBuddy-family providers: the CodeBuddy Code CLI and the WorkBuddy desktop app.

WorkBuddy embeds the CodeBuddy Code engine and only relocates its config
root, so both share one implementation and differ only in `CodeBuddyFamily`.
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from pathlib import Path

from lib import settings as settings_mod
from lib.providers.base import AgentProvider, ProviderCapabilities


@dataclass(frozen=True)
class CodeBuddyFamily:
    root_dirname: str
    config_dir_setting: str
    mcp_config_file: str
    mcp_needs_user_trust: bool


CODEBUDDY = CodeBuddyFamily(
    root_dirname=".codebuddy",
    config_dir_setting="codebuddy_config_dir",
    mcp_config_file=".mcp.json",
    mcp_needs_user_trust=False,
)

# The desktop app runs its engine with --strict-mcp-config, which ignores
# `.mcp.json`; its own UserMcpProvider reads `mcp.json` and gates each entry
# on a trust approval the user grants in the app.
WORKBUDDY = CodeBuddyFamily(
    root_dirname=".workbuddy",
    config_dir_setting="workbuddy_config_dir",
    mcp_config_file="mcp.json",
    mcp_needs_user_trust=True,
)


class CodeBuddyFamilyProvider(AgentProvider):
    family: CodeBuddyFamily = CODEBUDDY
    capabilities = ProviderCapabilities(
        skills=True,
        hooks=True,
        sessions=True,
        transcript_usage=True,
    )
    hook_config_format = "json"
    hook_output_format = "claude"
    transcript_format = "codebuddy"

    def __init__(self, overrides: dict | None = None):
        self._overrides = overrides or {}

    def config_root(self) -> Path:
        configured = getattr(settings_mod.settings, self.family.config_dir_setting)
        if configured:
            return Path(os.path.expanduser(str(configured)))
        return Path.home() / self.family.root_dirname

    def _path(self, key: str, default: Path) -> Path:
        raw = self._overrides.get(key)
        if raw in (None, ""):
            return default
        return Path(os.path.expanduser(str(raw)))

    def mcp_config_path(self) -> Path | None:
        return self.config_root() / self.family.mcp_config_file

    def global_skills_dir(self) -> Path:
        return self._path("skills_dir", self.config_root() / "skills")

    def project_skills_subpath(self) -> tuple[str, ...]:
        return (".codebuddy", "skills")

    def _skill_rel(self, skill_id: str, leaf: str) -> str:
        return f"{self.family.root_dirname}/skills/{skill_id}/{leaf}"

    def skill_invoke_path(self, skill_id: str) -> str:
        return self._skill_rel(skill_id, "invoke")

    def skill_launch_path(self, skill_id: str) -> str:
        return self._skill_rel(skill_id, "launch")

    def skill_content_relpath(self, skill_id: str) -> str:
        return self._skill_rel(skill_id, "content.md")

    def skill_id_from_read_path(self, file_path: str, *, home: str | None = None) -> str | None:
        if not file_path:
            return None
        home = home or os.path.expanduser("~")
        rel = file_path[len(home) + 1:] if file_path.startswith(home) else file_path
        pattern = rf"^{re.escape(self.family.root_dirname)}/skills/([^/]+)/content\.md$"
        m = re.match(pattern, rel)
        return m.group(1) if m else None

    def plans_dir(self) -> Path:
        return self._path("plans_dir", self.config_root() / "plans")

    def traces_dir(self) -> Path:
        return self._path("traces_dir", self.config_root() / "traces")

    def hook_settings_path(self) -> Path:
        return self._path("hook_settings_path", self.config_root() / "settings.json")

    def hook_manager_config_path(self) -> Path:
        return self._path("hook_manager_config_path",
                          self.config_root() / "hook-manager-config.json")

    def hook_payload_log_path(self) -> Path:
        return self._path("hook_payload_log_path",
                          self.config_root() / "hook-payloads.jsonl")

    def transcript_projects_dir(self) -> Path:
        return self._path("transcript_projects_dir", self.config_root() / "projects")

    def find_session_transcript(self, session_id: str) -> str | None:
        """`<projects>/<slug>/<session_id>.jsonl`. The sibling
        `<slug>/<session_id>/` directory holds only tool-result overflow
        files, so the one-level glob never descends into it."""
        if not session_id:
            return None
        pattern = str(self.transcript_projects_dir() / "*" / f"{session_id}.jsonl")
        matches = glob.glob(pattern)
        return max(matches, key=os.path.getmtime) if matches else None

    def resolve_transcript_path(self, payload) -> str | None:
        return (super().resolve_transcript_path(payload)
                or self.find_session_transcript(payload.session_id))

    def parse_transcript(self, transcript_path: str, *, max_text_bytes: int | None = None):
        from lib.trace.codebuddy_transcript import read_usage_codebuddy
        return read_usage_codebuddy(transcript_path, max_text_bytes=max_text_bytes)


class CodeBuddyProvider(CodeBuddyFamilyProvider):
    provider_id = "codebuddy"
    display_name = "CodeBuddy Code"
    family = CODEBUDDY


class WorkBuddyProvider(CodeBuddyFamilyProvider):
    provider_id = "workbuddy"
    display_name = "WorkBuddy"
    family = WORKBUDDY
