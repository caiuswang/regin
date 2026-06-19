"""Kimi Code CLI provider adapter.

Moonshot's Kimi Code CLI (`kimi`, https://moonshotai.github.io/kimi-code/)
ships a Claude-grade lifecycle-hook system: events are delivered as JSON on
stdin, exit code 2 on a PreToolUse hook blocks the call, and a PreToolUse
hook may additionally return a ``hookSpecificOutput.permissionDecision`` to
deny. The event names and the common payload fields (``session_id``, ``cwd``,
``hook_event_name``, ``tool_name``, ``tool_input``) line up 1:1 with the
schema ``hook_manager`` already normalizes, so capture needs only:

* this adapter (paths live under ``~/.kimi-code`` — verified against the
  installed CLI, *not* the ``~/.kimi`` the public docs still show);
* two field aliases in ``hook_manager.core`` (``tool_call_id`` →
  ``tool_use_id``, ``tool_output`` → ``tool_response``); and
* a TOML install path (Kimi reads hooks from ``config.toml`` ``[[hooks]]``
  rather than Claude's ``settings.json``).

Capabilities cover span capture, transcript-usage ingestion (Kimi's own
``wire.jsonl`` session format, protocol_version 1.4), and managed skill
deployment: the Kimi CLI natively loads skills (``kimi --skills-dir`` and
auto-discovery of the user/project ``.kimi-code/skills`` dirs), and the
provider-agnostic deployer writes the standard ``SKILL.md`` layout there.
"""

from __future__ import annotations

import glob
import os
import re
from pathlib import Path

from lib.providers.base import AgentProvider, ProviderCapabilities
from hook_manager.core import HookPayload, HookResponse, PermissionRequestInfo


_KIMI_HOME = Path.home() / ".kimi-code"
_SKILL_READ_CONTENT_RE = re.compile(
    r"^(?:\.kimi-code|\.agents)/skills/([^/]+)/content\.md$"
)

# Kimi's Read tool appends a `<system>… Total lines in file: N …</system>`
# footer after the (cat -n line-numbered) file body. Strip it from the content
# the Read card shows, and lift the line count onto `total_lines` for the
# detail panel.
_KIMI_READ_FOOTER_RE = re.compile(r"\n?<system>.*?</system>\s*$", re.DOTALL)
_KIMI_READ_TOTAL_LINES_RE = re.compile(r"Total lines in file:\s*(\d+)")


def _kimi_read_file_info(output: str) -> dict:
    """Build the Claude-shaped ``tool_response['file']`` dict from Kimi's Read
    output blob: the line-numbered body (footer stripped) plus the total line
    count Kimi reports in its trailing ``<system>`` annotation."""
    info: dict = {}
    footer = _KIMI_READ_FOOTER_RE.search(output)
    if footer:
        total = _KIMI_READ_TOTAL_LINES_RE.search(footer.group(0))
        if total:
            info['total_lines'] = int(total.group(1))
    info['content'] = _KIMI_READ_FOOTER_RE.sub('', output)
    return info

# Kimi also discovers skills in the cross-agent `~/.agents/skills/` directory
# (and `.agents/skills/` at project scope). `_SKILL_READ_CONTENT_RE` recognizes
# reads from either location so skill-usage traces capture both Kimi-specific
# and shared agent skill trees.

# Kimi Code CLI's full lifecycle-hook surface (13 events). Every one is a
# member of hook_manager.core.SPEC_EVENTS already, so the router accepts them
# without change. We install the events regin has trace handlers for.
_KIMI_HOOK_EVENTS: tuple[str, ...] = (
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "StopFailure",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "PostCompact",
    "Notification",
)


class KimiProvider(AgentProvider):
    provider_id = "kimi"
    display_name = "Kimi Code"
    capabilities = ProviderCapabilities(
        # Kimi loads skills from ~/.kimi-code/skills (auto-discovered) and via
        # `kimi --skills-dir`; the deployer writes the standard SKILL.md layout
        # there through the skill path methods defined below.
        skills=True,
        hooks=True,
        sessions=True,
        # Kimi's wire.jsonl is parsed by lib.trace.kimi_transcript, giving
        # prompt text + per-turn assistant/thinking text + token usage.
        transcript_usage=True,
    )

    # Kimi stores hooks inside the main config.toml (a TOML [[hooks]] array),
    # not a JSON settings file. The hooks blueprint branches on this to pick
    # the right reader/writer.
    hook_config_format = "toml"
    # Kimi Code parses only its own tiny hook-output contract
    # (hookSpecificOutput.permissionDecision); any other stdout JSON — e.g.
    # Claude's `{"suppressOutput": true}` — is rendered verbatim in the UI. The
    # runner uses this to emit Kimi's shape (or nothing) instead.
    hook_output_format = "kimi"
    # Kimi's session file is the event-sourced wire.jsonl, parsed by
    # lib.trace.kimi_transcript rather than Claude's read_usage.
    transcript_format = "kimi"

    def __init__(self, overrides: dict | None = None):
        self._overrides = overrides or {}

    def hook_events(self) -> tuple[str, ...] | None:
        return _KIMI_HOOK_EVENTS

    def permission_request_events(self) -> tuple[str, ...]:
        # Kimi gates tool calls through PreToolUse (exit 2 / permissionDecision),
        # but regin does not mediate Kimi's permission prompts in this milestone.
        return ("PreToolUse",)

    def build_permission_request_info(self, payload: HookPayload) -> PermissionRequestInfo | None:
        # Kimi PreToolUse events are not interactive permission requests that
        # regin resolves on the user's behalf, so there is nothing to surface.
        return None

    def serialize_permission_decision(
        self,
        info: PermissionRequestInfo,
        selected_option_id: str | None = None,
    ) -> HookResponse:
        return HookResponse()

    def tool_failure_error_text(self, raw_error: object) -> str:
        """Kimi's PostToolUseFailure carries a structured error object
        (``{code, message, retryable}``) rather than Claude's bare string.
        Surface the ``message`` so the failure span shows the real error
        instead of crashing the shared handler on ``dict.strip()``."""
        if isinstance(raw_error, str):
            return raw_error.strip()
        if isinstance(raw_error, dict):
            msg = raw_error.get('message')
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
            code = raw_error.get('code')
            return code.strip() if isinstance(code, str) else ''
        return ''

    def normalize_tool_response(
        self, tool_name: str, tool_input: dict, tool_response: dict
    ) -> dict:
        """Map Kimi's single ``{output, isError}`` result envelope onto the
        Claude-shaped keys the shared `post_tool_trace` builders read.

        Kimi returns every tool result as one text blob under ``output`` (plus
        an ``isError`` flag), where regin's builders expect tool-specific
        fields — ``stdout``/``stderr`` for Bash, ``file.content`` for Read —
        so without this both cards render an empty body. Tools whose card
        attrs derive purely from ``tool_input`` (Edit/Write diffs, Grep/Glob
        pattern) need no result mapping and pass through. The original keys are
        preserved; we only *add* the canonical ones (``setdefault``) so a
        future Kimi payload that already carries them wins.
        """
        if not isinstance(tool_response, dict):
            return tool_response
        output = tool_response.get('output')
        if not isinstance(output, str) or not output:
            return tool_response
        out = dict(tool_response)
        if tool_name == 'Bash':
            # Kimi merges stdout+stderr into one stream; surface it as stdout
            # (the Bash card shows stderr separately only when present).
            out.setdefault('stdout', output)
        elif tool_name == 'Read':
            out.setdefault('file', _kimi_read_file_info(output))
        return out

    def tool_failure_is_user_rejection(self, raw_error: object) -> bool:
        """Kimi reports a rejected permission prompt as a PostToolUseFailure
        whose message reads "... was not run because the user rejected the
        approval request." That same rejection is logged to wire.jsonl and
        materialized as a `tooldeny-*` span by the transcript scan, so the
        shared failure handler must NOT also emit a `tool.failure` for it —
        otherwise the one rejected call renders twice (a red failure + the
        amber deny). Match the rejection wording; genuine tool errors (e.g.
        a non-zero shell exit) don't carry it and still get a failure span."""
        msg = self.tool_failure_error_text(raw_error).lower()
        if not msg:
            return False
        return (
            'rejected the approval request' in msg
            or 'was not run because the user' in msg
        )

    def reconcile_subagents(self, session_id: str) -> None:
        """Kimi fires a subagent's PreToolUse/PostToolUse under the PARENT
        session_id, so the sub-tool/turn spans land flat on the parent trace.
        Trigger the server-side reconciler to read this subagent's own wire and
        nest them under the subagent trace. See
        lib/trace/kimi_subagents.reconcile_kimi_subagents."""
        from lib.hook_plugin import post_event  # type: ignore
        post_event('kimi_subagents', {'trace_id': session_id})

    def _path(self, key: str, default: Path) -> Path:
        raw = self._overrides.get(key)
        if raw in (None, ""):
            return default
        return Path(os.path.expanduser(str(raw)))

    def global_skills_dir(self) -> Path:
        return self._path("skills_dir", _KIMI_HOME / "skills")

    def project_skills_subpath(self) -> tuple[str, ...]:
        return (".kimi-code", "skills")

    def skill_invoke_path(self, skill_id: str) -> str:
        return f".kimi-code/skills/{skill_id}/invoke"

    def skill_launch_path(self, skill_id: str) -> str:
        return f".kimi-code/skills/{skill_id}/launch"

    def skill_content_relpath(self, skill_id: str) -> str:
        return f".kimi-code/skills/{skill_id}/content.md"

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
        return self._path("plans_dir", _KIMI_HOME / "plans")

    def traces_dir(self) -> Path:
        return self._path("traces_dir", _KIMI_HOME / "traces")

    def hook_settings_path(self) -> Path:
        # The TOML file Kimi reads hooks from.
        return self._path("hook_settings_path", _KIMI_HOME / "config.toml")

    def hook_manager_config_path(self) -> Path:
        return self._path("hook_manager_config_path", _KIMI_HOME / "hook-manager-config.json")

    def hook_payload_log_path(self) -> Path:
        return self._path("hook_payload_log_path", _KIMI_HOME / "hook-payloads.jsonl")

    def transcript_projects_dir(self) -> Path:
        return self._path("transcript_projects_dir", _KIMI_HOME / "sessions")

    def resolve_transcript_path(self, payload: HookPayload) -> str | None:
        """Locate a session's wire.jsonl from its id.

        Kimi hook payloads don't carry a transcript path, so we glob for
        ``<sessions>/wd_*/<session_id>/agents/main/wire.jsonl`` (the working
        directory segment is opaque). Returns the most recently modified match
        when more than one is present.
        """
        session_id = payload.session_id
        if not session_id:
            return None
        base = self.transcript_projects_dir()
        pattern = str(base / "*" / session_id / "agents" / "main" / "wire.jsonl")
        matches = glob.glob(pattern)
        if not matches:
            return None
        return max(matches, key=os.path.getmtime)

    def parse_transcript(self, transcript_path: str, *, max_text_bytes: int | None = None):
        from lib.trace.kimi_transcript import read_usage_kimi
        return read_usage_kimi(transcript_path, max_text_bytes=max_text_bytes)
