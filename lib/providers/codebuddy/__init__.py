"""CodeBuddy-family providers: the CodeBuddy Code CLI and the WorkBuddy desktop app.

WorkBuddy embeds the CodeBuddy Code engine and only relocates its config
root, so both share one implementation and differ only in `CodeBuddyFamily`.
"""

from __future__ import annotations

import functools
import glob
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from lib import settings as settings_mod
from lib.providers.base import (AgentProvider, LaunchCapabilities,
                                ProviderCapabilities)


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

# What `codebuddy --model` names as currently supported. A menu, not a
# whitelist: the launch routes take any string, and this list only decides
# what is one pick away, so an id the CLI has since added still goes through
# the sheet's own custom entry.
MODELS = (
    "hy4-preview",
    "hy3",
    "hy3-x",
    "step-5-preview",
    "space-bunny",
    "glm-5.3",
    "glm-5.3-flash",
    "glm-5.2",
    "glm-5.1",
    "glm-5v-turbo",
    "minimax-m3",
    "minimax-m3.1-flash",
    "minimax-m2.7",
    "kimi-k3-1",
    "kimi-k2.8-preview",
    "kimi-k2.7",
    "kimi-k2.6",
    "deepseek-v4-pro",
    "deepseek-v4.1-flash",
    "mimo-v2.6-pro",
)


class CodeBuddyFamilyProvider(AgentProvider):
    family: CodeBuddyFamily = CODEBUDDY
    experimental = False
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

    @property
    def mcp_needs_user_trust(self) -> bool:
        return self.family.mcp_needs_user_trust

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

    def launch_capabilities(self) -> LaunchCapabilities:
        return {
            "launch_modes": ["sdk", "tmux"],
            # The permission tiers `--permission-mode` takes and the ones its
            # ACP `session/set_mode` answers to are Claude's own six, so the
            # shared vocabulary — arm's validation, the sheet's menu — fits
            # without a second list.
            "fields": ["model", "permission_mode"],
            "binary": "codebuddy",
            # `codebuddy` is a `#!/usr/bin/env node` script.
            "pane_commands": ["node"],
            "labels": {"sdk": "ACP", "tmux": "tmux"},
            "models": list(MODELS),
            "default_model": "",
        }

    def pane_argv(self, session_id: str, *, model: str = "", effort: str = "",
                  permission_mode: str = "", prompt: str = "",
                  resume: bool = False) -> list[str]:
        # Absolute: the tmux server may predate this process and not carry
        # its PATH.
        argv = [shutil.which("codebuddy") or "codebuddy",
                "--resume" if resume else "--session-id", session_id]
        # An unarmed run is regin's own, with nobody at the pane to answer a
        # prompt, so it keeps the CLI's skip flag; an armed tier replaces it,
        # since `plan` under the skip flag would not be plan.
        if permission_mode:
            argv += ["--permission-mode", permission_mode]
        else:
            argv += ["--dangerously-skip-permissions"]
        if model:
            argv += ["--model", model]
        if prompt:
            argv += ["--", prompt]
        return argv

    def channel_launcher(self):
        from lib.agent_acp.launcher import launch_run
        return functools.partial(launch_run, provider=self)

    def acp_argv(self) -> list[str]:
        from lib.agent_acp.client import binary_argv
        return binary_argv("codebuddy", "--acp")

    # codebuddy offers no `yolo`; its "Ask" tiers would stall on every call.
    acp_unattended_mode = "bypassPermissions"


class WorkBuddyProvider(CodeBuddyFamilyProvider):
    provider_id = "workbuddy"
    display_name = "WorkBuddy"
    family = WORKBUDDY
