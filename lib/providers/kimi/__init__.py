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
import json
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
_KIMI_READ_SPAN_RE = re.compile(r"(\d+) lines? read from file starting from line (\d+)")


def _kimi_read_footer_stats(info: dict, footer: str) -> None:
    total = _KIMI_READ_TOTAL_LINES_RE.search(footer)
    if total:
        info['total_lines'] = int(total.group(1))
    span = _KIMI_READ_SPAN_RE.search(footer)
    if span:
        info['num_lines'] = int(span.group(1))
        info['start_line'] = int(span.group(2))


def _kimi_read_input_stats(info: dict, tool_input: dict) -> None:
    for key, field in (('start_line', 'line_offset'), ('num_lines', 'n_lines')):
        val = tool_input.get(field)
        if key not in info and isinstance(val, int) and not isinstance(val, bool):
            info[key] = val


def _kimi_read_file_info(output: str, tool_input: dict) -> dict:
    """Build the Claude-shaped ``tool_response['file']`` dict from Kimi's Read
    output blob: the line-numbered body (footer stripped) plus the line slice
    it covers.

    Kimi appends a ``<system>`` annotation carrying the slice ("N lines read
    from file starting from line M") and the file's total length; the slice
    falls back to the request's own ``line_offset`` / ``n_lines`` when that
    annotation is absent.
    """
    info: dict = {}
    footer = _KIMI_READ_FOOTER_RE.search(output)
    if footer:
        _kimi_read_footer_stats(info, footer.group(0))
    _kimi_read_input_stats(info, tool_input)
    info['content'] = _KIMI_READ_FOOTER_RE.sub('', output)
    return info


# Kimi's background-task tools (a backgrounded `Bash`, and the `TaskOutput`
# that later collects it) return one text blob whose leading `key: value`
# lines are the task record and whose `[output]` section is the captured
# program output.
_KIMI_TASK_BODY_SEP = "\n[output]\n"
_KIMI_TASK_HEADER_RE = re.compile(r"^([a-z][a-z0-9_]*): (.*)$")
_KIMI_INT_RE = re.compile(r"-?\d+$")
# Kimi's task-record field name → the Claude `task` key the shared TaskOutput
# builder reads.
_KIMI_TASK_FIELDS: tuple[tuple[str, str], ...] = (
    ('task_id', 'task_id'),
    ('description', 'description'),
    ('status', 'status'),
    ('task_type', 'kind'),
)


def _kimi_task_header(output: str) -> tuple[dict, str]:
    head, sep, body = output.partition(_KIMI_TASK_BODY_SEP)
    fields: dict = {}
    for line in head.split('\n'):
        m = _KIMI_TASK_HEADER_RE.match(line)
        if m:
            fields.setdefault(m.group(1), m.group(2))
    return fields, body if sep else ''


def _kimi_task_result(output: str, tool_input: dict, tool_response: dict) -> dict:
    """Reshape a Kimi ``TaskOutput`` blob into Claude's
    ``{retrieval_status, task: {...}}`` envelope. Without it the span carries
    only the polled task id — no completed/failed signal and no output body."""
    fields, body = _kimi_task_header(output)
    task: dict = {}
    for claude_key, kimi_key in _KIMI_TASK_FIELDS:
        val = fields.get(kimi_key)
        if val:
            task[claude_key] = val
    exit_code = fields.get('exit_code') or ''
    if _KIMI_INT_RE.match(exit_code):
        task['exit_code'] = int(exit_code)
    if body.strip():
        task['output'] = body
    if not task:
        return {}
    result = {'task': task}
    retrieval = fields.get('retrieval_status')
    if retrieval:
        result['retrieval_status'] = retrieval
    return result


def _kimi_is_error(tool_response: dict) -> bool:
    return bool(tool_response.get('isError') or tool_response.get('is_error'))


def _kimi_bash_result(output: str, tool_input: dict, tool_response: dict) -> dict:
    """Kimi merges stdout+stderr into one stream, so the whole blob lands on
    ``stdout`` — unless the envelope flags the call as an error, where the Bash
    card's red ``stderr`` block is the honest rendering. A backgrounded Bash
    returns the task record instead of program output; lift its ``task_id`` so
    the card shows its background chip."""
    stream = 'stderr' if _kimi_is_error(tool_response) else 'stdout'
    result = {stream: output}
    if tool_input.get('run_in_background'):
        task_id = _kimi_task_header(output)[0].get('task_id')
        if task_id:
            result['background_task_id'] = task_id
    return result


def _kimi_read_result(output: str, tool_input: dict, tool_response: dict) -> dict:
    return {'file': _kimi_read_file_info(output, tool_input)}


def _kimi_ask_result(output: str, tool_input: dict, tool_response: dict) -> dict:
    """Kimi returns the AskUserQuestion result as a JSON string under
    ``output`` (``{"answers": {...}}``); the shared `_build_ask_attrs` reads
    ``answers``/``annotations`` off the top-level response, so parse the blob
    and lift them. A non-JSON output (user dismissed the prompt) yields
    nothing — the card then shows just the questions, which is the truth."""
    try:
        parsed = json.loads(output)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {k: parsed[k] for k in ('answers', 'annotations') if parsed.get(k)}


def _newest_dir(pattern: Path) -> Path | None:
    matches = [p for p in glob.glob(str(pattern)) if os.path.isdir(p)]
    if not matches:
        return None
    return Path(max(matches, key=os.path.getmtime))


_KIMI_RESULT_BUILDERS = {
    'AskUserQuestion': _kimi_ask_result,
    'Bash': _kimi_bash_result,
    'Read': _kimi_read_result,
    'TaskOutput': _kimi_task_result,
}

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
        fields — ``stdout``/``stderr`` for Bash, ``file.content`` for Read,
        ``retrieval_status``/``task`` for TaskOutput — so without this the
        cards render an empty body. Tools whose card attrs derive purely from
        ``tool_input`` (Edit/Write diffs, Grep/Glob pattern) need no result
        mapping and pass through. The original keys are preserved; we only
        *add* the canonical ones (``setdefault``) so a future Kimi payload that
        already carries them wins.
        """
        if not isinstance(tool_response, dict):
            return tool_response
        output = tool_response.get('output')
        if not isinstance(output, str) or not output:
            return tool_response
        builder = _KIMI_RESULT_BUILDERS.get(tool_name)
        if builder is None:
            return tool_response
        out = dict(tool_response)
        for key, value in builder(output, tool_input or {}, tool_response).items():
            out.setdefault(key, value)
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

    def session_plans_dir(self, session_id: str) -> Path | None:
        """The plans directory of one Kimi session, or None when it has none.

        Mirrors `resolve_transcript_path`: the working-directory segment of the
        session path is opaque, so it is globbed."""
        if not session_id:
            return None
        return _newest_dir(
            self.transcript_projects_dir() / "*" / session_id / "agents" / "main" / "plans"
        )

    def plans_dir(self) -> Path:
        """Kimi has no global plans directory — plan markdown is written per
        session under ``<sessions>/<wd>/<session_id>/agents/main/plans``. The
        plan scanner and the plan-file matcher both ask for a single path with
        no session in hand, so resolve to the most recently written of those.
        The legacy ``~/.kimi-code/plans`` is only the (never-written) fallback
        for an install that has no plan at all yet."""
        if self._overrides.get("plans_dir") not in (None, ""):
            return self._path("plans_dir", _KIMI_HOME / "plans")
        newest = _newest_dir(
            self.transcript_projects_dir() / "*" / "*" / "agents" / "main" / "plans"
        )
        return newest or _KIMI_HOME / "plans"

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

    def find_session_transcript(self, session_id: str) -> str | None:
        """Locate a session's wire.jsonl from its id.

        Kimi stores a session as a *directory*
        (``<sessions>/wd_*/<session_id>/agents/main/wire.jsonl``) rather than
        Claude's ``<session_id>.jsonl`` file, and the working-directory segment
        is opaque, so it is globbed. Returns the most recently modified match
        when more than one is present.
        """
        if not session_id:
            return None
        base = self.transcript_projects_dir()
        pattern = str(base / "*" / session_id / "agents" / "main" / "wire.jsonl")
        matches = glob.glob(pattern)
        if not matches:
            return None
        return max(matches, key=os.path.getmtime)

    def resolve_transcript_path(self, payload: HookPayload) -> str | None:
        """Kimi hook payloads carry no transcript path, so resolve from the id."""
        return self.find_session_transcript(payload.session_id)

    def parse_transcript(self, transcript_path: str, *, max_text_bytes: int | None = None):
        from lib.trace.kimi_transcript import read_usage_kimi
        return read_usage_kimi(transcript_path, max_text_bytes=max_text_bytes)
