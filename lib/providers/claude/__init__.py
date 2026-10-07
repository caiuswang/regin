"""Claude provider adapter."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from lib.providers.base import (AgentProvider, LaunchCapabilities,
                                ProviderCapabilities, SkillCatalogLayout)
from hook_manager.core import (
    HookPayload,
    HookResponse,
    PermissionOption,
    PermissionRequestDecision,
    PermissionRequestInfo,
)


_SKILL_READ_CONTENT_RE = re.compile(r"^\.claude/skills/([^/]+)/content\.md$")

# Tools whose permission `acceptEdits` ("auto-accept edits") grants without
# prompting. Everything else (Bash, WebFetch, …) still stops for a human even
# in that mode, so the request stays a real blocker.
_AUTO_ACCEPTED_EDIT_TOOLS = frozenset(
    {"Edit", "Write", "MultiEdit", "NotebookEdit"})


class ClaudeProvider(AgentProvider):
    provider_id = "claude"
    display_name = "Claude Code"
    capabilities = ProviderCapabilities(
        skills=True,
        hooks=True,
        sessions=True,
        transcript_usage=True,
    )
    session_id_env_vars = ("CLAUDE_CODE_SESSION_ID",)

    def __init__(self, overrides: dict | None = None, *, legacy_skills_dir: Path | None = None):
        self._overrides = overrides or {}
        self._legacy_skills_dir = legacy_skills_dir

    def _path(self, key: str, default: Path) -> Path:
        raw = self._overrides.get(key)
        if raw in (None, ""):
            return default
        return Path(os.path.expanduser(str(raw)))

    def global_skills_dir(self) -> Path:
        if self._legacy_skills_dir is not None and "skills_dir" not in self._overrides:
            return Path(self._legacy_skills_dir)
        return self._path("skills_dir", Path.home() / ".claude" / "skills")

    def project_skills_subpath(self) -> tuple[str, ...]:
        return (".claude", "skills")

    def skill_catalog_layout(self) -> SkillCatalogLayout:
        return SkillCatalogLayout(skill_dirs=((".claude", "skills"),),
                                  command_dirs=((".claude", "commands"),),
                                  plugins=True)

    def launch_capabilities(self) -> LaunchCapabilities:
        from lib.agent_sdk import client
        from lib.settings import settings
        return {
            "launch_modes": ["sdk", "tmux"],
            "fields": ["model", "permission_mode", "effort"],
            "binary": "claude",
            "pane_commands": ["claude", "claude.exe", "node"],
            "labels": {"sdk": "Agent SDK", "tmux": "tmux"},
            "models": list(client.MODEL_CHOICES),
            "default_model": settings.agent_sdk.model or "",
        }

    def pane_argv(self, session_id: str, *, model: str = "", effort: str = "",
                  permission_mode: str = "", prompt: str = "",
                  resume: bool = False) -> list[str]:
        # Absolute: the tmux server may predate this process and does not carry
        # its PATH, and an nvm-installed claude is on no system path.
        # `--resume` without `--fork-session` keeps the session's own id, so the
        # id is still regin's to name — it is just not a fresh one.
        argv = [shutil.which("claude") or "claude",
                "--resume" if resume else "--session-id", session_id]
        for flag, value in (("--model", model), ("--effort", effort),
                            ("--permission-mode", permission_mode)):
            if value:
                argv += [flag, value]
        if prompt:
            # `--` ends option parsing: a prompt opening with `-` (a markdown
            # bullet, or `--dangerously-skip-permissions`) is a first turn, never a flag.
            argv += ["--", prompt]
        return argv

    def client_version(self) -> str | None:
        from lib.trace.claude_version import current_claude_version
        return current_claude_version()

    def reconcile_subagents(self, session_id: str, *, live: bool = False) -> None:
        """Attribute Task-tool subagent API spend onto the session bill.

        Claude writes each subagent's conversation to a sibling
        ``subagents/agent-*.jsonl`` (not as parent-transcript sidechains), so
        its token spend is otherwise invisible to ``turn_usage``. Stamp each
        subagent's total onto its ``subagent.stop`` marker so the rollup's
        ``subagent_*`` line reflects it. Best-effort: never let a reconcile
        failure break the SubagentStop hook."""
        try:
            from lib.trace.claude_subagents import reconcile_claude_subagents
            reconcile_claude_subagents(session_id)
        except Exception:
            pass

    def permission_request_events(self) -> tuple[str, ...]:
        return ("PermissionRequest",)

    def build_permission_request_info(self, payload: HookPayload) -> PermissionRequestInfo | None:
        if payload.event != "PermissionRequest":
            return None
        suggestions = payload.raw.get("permission_suggestions") or []
        if not isinstance(suggestions, list):
            suggestions = []
        options = _options_from_suggestions(suggestions)
        if not options:
            options = [PermissionOption(
                id="deny",
                label="Deny",
                description="Deny this permission request.",
            )]
        return PermissionRequestInfo(
            tool_name=payload.tool_name,
            tool_input_summary=_summarize_tool_input(payload.tool_input),
            cwd=payload.cwd,
            permission_mode=payload.permission_mode,
            requested_permission=_requested_permission(payload.tool_name, payload.tool_input),
            suggestions=suggestions,
            options=options,
            default_option_id=options[0].id,
        )

    def serialize_permission_decision(
        self,
        info: PermissionRequestInfo,
        selected_option_id: str | None = None,
    ) -> HookResponse:
        option = _select_option(info, selected_option_id)
        if option is None or option.id == "deny":
            return HookResponse(permission_request_decision=PermissionRequestDecision(
                behavior="deny",
                message="permission denied",
            ))
        return HookResponse(permission_request_decision=PermissionRequestDecision(
            behavior="allow",
            updated_permissions=option.updated_permissions,
        ))

    def permission_awaits_human(self, payload: HookPayload) -> bool:
        """Resolve "does a human actually have to decide this?" for Claude
        Code's four permission modes (the full set):

          * ``bypassPermissions`` — every tool auto-allowed; no prompt ever
            reaches a person → never a blocker.
          * ``acceptEdits`` — file-edit tools auto-accepted, but other tools
            (Bash, WebFetch, …) still prompt → a blocker only for non-edit
            tools.
          * ``plan`` — the run is paused for the human to approve/reject the
            plan, and edits/commands are gated → a blocker.
          * ``default`` (and an absent/unknown mode) — asks unless a standing
            allow/deny rule already matched; since the harness only fires
            ``PermissionRequest`` when it decided to ask, treat it as a
            blocker.

        ``AskUserQuestion`` is the exception that ignores the mode entirely:
        it is the agent *directly asking the user a question*, not a tool the
        harness gates. ``bypassPermissions`` / ``acceptEdits`` skip
        tool-permission prompts, but the question UI still surfaces and waits
        for an answer — so it always blocks on a human, in every mode.
        """
        if payload.tool_name == "AskUserQuestion":
            return True
        mode = payload.permission_mode
        if mode == "bypassPermissions":
            return False
        if mode == "acceptEdits":
            return payload.tool_name not in _AUTO_ACCEPTED_EDIT_TOOLS
        return True

    def skill_invoke_path(self, skill_id: str) -> str:
        return f".claude/skills/{skill_id}/invoke"

    def skill_launch_path(self, skill_id: str) -> str:
        return f".claude/skills/{skill_id}/launch"

    def skill_content_relpath(self, skill_id: str) -> str:
        return f".claude/skills/{skill_id}/content.md"

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
        return self._path("plans_dir", Path.home() / ".claude" / "plans")

    def traces_dir(self) -> Path:
        return self._path("traces_dir", Path.home() / ".claude" / "traces")

    def hook_settings_path(self) -> Path:
        return self._path("hook_settings_path", Path.home() / ".claude" / "settings.json")

    def hook_manager_config_path(self) -> Path:
        return self._path("hook_manager_config_path", Path.home() / ".claude" / "hook-manager-config.json")

    def hook_payload_log_path(self) -> Path:
        return self._path("hook_payload_log_path", Path.home() / ".claude" / "hook-payloads.jsonl")

    def transcript_projects_dir(self) -> Path:
        return self._path("transcript_projects_dir", Path.home() / ".claude" / "projects")

    def live_sessions(self) -> dict[str, str] | None:
        """`claude agents --json` — every session id the CLI still HOLDS.

        Holds, not "is mid-turn": a parked conversation reports
        `status: idle, state: done` and is listed all the same, because the CLI
        is still the process that owns the id. That is the predicate resume
        needs — `--resume` against a held id starts a *copy* rather than
        continuing the conversation — so the per-row `status`/`state` must not
        be read as the answer. Some rows carry neither field.

        Covers both `kind`s it reports: `interactive` (a claude in someone's
        terminal) and `background` (one the CLI's own daemon holds on a private
        PTY). Without `--all` the listing is exactly the held set — that flag
        is what adds background sessions the CLI has let go of — so membership
        is the whole answer.

        The handle is the row's `id`, which is what `claude attach` takes.
        Only background rows carry one: a claude someone is driving in their
        own terminal is not something a second client may open, and the CLI
        says so by omitting the field.

        Every failure degrades to None rather than an empty dict, which leaves
        callers exactly where they were before this method existed. `claude`
        may not be on PATH at all: regin ingests traces from installs it does
        not own.
        """
        import json
        import subprocess

        try:
            r = subprocess.run(["claude", "agents", "--json"],
                               capture_output=True, text=True,
                               timeout=_AGENTS_QUERY_TIMEOUT_SEC)
            if r.returncode != 0:
                return None
            rows = json.loads(r.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if not isinstance(rows, list):
            return None
        out: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            sid, handle = row.get("sessionId"), row.get("id")
            if isinstance(sid, str) and sid:
                out[sid] = handle if isinstance(handle, str) else ""
        return out


# Asked on the trace-view poll, so it must never become the reason a page
# hangs; the call measures ~0.12s against a warm daemon.
_AGENTS_QUERY_TIMEOUT_SEC = 3.0


def _summarize_tool_input(tool_input: dict) -> dict:
    from lib.trace.tool_input_summary import summarize_tool_input
    return summarize_tool_input(tool_input, include_replace_all=True)


def _requested_permission(tool_name: str | None, tool_input: dict) -> str:
    if tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str) and command:
            return f"Run shell command: {command[:500]}"
    if tool_name in {"Read", "Write", "Edit", "MultiEdit"}:
        path = tool_input.get("file_path") or tool_input.get("path")
        if isinstance(path, str) and path:
            verb = "Read" if tool_name == "Read" else "Modify"
            return f"{verb} file: {path[:500]}"
    return f"Use tool: {tool_name or 'unknown'}"


def _options_from_suggestions(suggestions: list[dict]) -> list[PermissionOption]:
    options: list[PermissionOption] = []
    for i, suggestion in enumerate(suggestions):
        if not isinstance(suggestion, dict):
            continue
        options.append(PermissionOption(
            id=_option_id(suggestion, i),
            label=_option_label(suggestion),
            description=_option_description(suggestion),
            updated_permissions=[suggestion],
        ))
    options.append(PermissionOption(
        id="deny",
        label="Deny",
        description="Deny this permission request.",
    ))
    return options


def _option_id(suggestion: dict, index: int) -> str:
    destination = str(suggestion.get("destination") or "session")
    if suggestion.get("type") == "setMode":
        return f"set_mode_{suggestion.get('mode') or 'unknown'}_{destination}"
    behavior = suggestion.get("behavior") or "allow"
    return f"{behavior}_{destination}_{index + 1}"


def _option_label(suggestion: dict) -> str:
    destination = suggestion.get("destination") or "session"
    if suggestion.get("type") == "setMode":
        return f"Set mode {suggestion.get('mode')} for {destination}"
    behavior = suggestion.get("behavior") or "allow"
    return f"{str(behavior).title()} for {destination}"


def _option_description(suggestion: dict) -> str:
    rules = suggestion.get("rules")
    if isinstance(rules, list) and rules:
        first = rules[0]
        if isinstance(first, dict):
            tool = first.get("toolName") or first.get("tool_name")
            content = first.get("ruleContent") or first.get("rule_content")
            if tool and content:
                return f"{tool}: {content}"
            if tool:
                return str(tool)
    mode = suggestion.get("mode")
    if mode:
        return f"Switch permission mode to {mode}."
    return "Apply the suggested permission update."


def _select_option(info: PermissionRequestInfo, selected_option_id: str | None) -> PermissionOption | None:
    wanted = selected_option_id or info.default_option_id
    for option in info.options:
        if option.id == wanted:
            return option
    return None
