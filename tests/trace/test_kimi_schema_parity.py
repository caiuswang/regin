"""Kimi payload-schema parity.

Kimi's hook+tool payloads inherit Claude's committed schemas by lineage
(`_SCHEMA_PARENT = {'kimi': 'claude'}`), but Kimi's real payload surface
diverges in a handful of ways that used to spam `payload_schema_drift`:

  * every tool carries Kimi-native `tool_call_id` / `tool_output` aliases
    and a single `tool_response.output` result blob;
  * Read/Edit/Write use `tool_input.path` (not `file_path`), Bash adds
    `cwd`, and the tool names `FetchURL` / `TodoList` have no Claude schema;
  * `UserPromptSubmit.prompt` is a content-block array, `PostToolUseFailure`
    carries a structured `{code, message, retryable}` error object, the
    subagent events add `agent_name` + prompt/response, and `PreCompact` /
    `PostCompact` had no committed schema at all.

These cases are now covered by central normalization (alias pop +
`tool_response.output` recognition) plus Kimi baseline schemas. This test
pins every one to zero drift so a future Claude-schema edit or a regression
in the alias handling can't silently start flagging Kimi traffic again.
"""

from __future__ import annotations

import pytest

from hook_manager.core import _normalize_payload
from lib.trace.payload_validation import validate, validate_event


def _drift(payload: dict) -> list:
    """Normalize a raw Kimi payload the way the hook pipeline does, then
    run it through the right validator for its subject_kind axis."""
    norm = _normalize_payload(payload)
    event = norm.get("hook_event_name")
    if event == "PostToolUse":
        return validate(norm.get("tool_name"), norm, agent="kimi")
    return validate_event(event, norm, agent="kimi")


# ── tool (PostToolUse) payloads, in Kimi's native shape ──────────────

_BASH = {
    "hook_event_name": "PostToolUse", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "tool_name": "Bash", "tool_call_id": "tool_a",
    "tool_input": {"command": "ls", "cwd": "/r"},
    "tool_output": "a\nb\n",
    "tool_response": {"output": "a\nb\n"},
}
_READ = {
    "hook_event_name": "PostToolUse", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "tool_name": "Read", "tool_call_id": "tool_b",
    "tool_input": {"path": "pyproject.toml", "line_offset": 0, "n_lines": 21},
    "tool_output": "1\t[project]\n",
    "tool_response": {"output": "1\t[project]\n"},
}
_EDIT = {
    "hook_event_name": "PostToolUse", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "tool_name": "Edit", "tool_call_id": "tool_c",
    "tool_input": {"path": "a.py", "old_string": "x", "new_string": "y"},
    "tool_response": {"output": "ok"},
}
_WRITE = {
    "hook_event_name": "PostToolUse", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "tool_name": "Write", "tool_call_id": "tool_d",
    "tool_input": {"path": "a.py", "content": "print(1)\n"},
    "tool_response": {"output": "ok"},
}
_GLOB = {
    "hook_event_name": "PostToolUse", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "tool_name": "Glob", "tool_call_id": "tool_e",
    "tool_input": {"pattern": "**/*.py", "path": "."},
    "tool_response": {"output": "a.py\nb.py"},
}
_GREP = {
    "hook_event_name": "PostToolUse", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "tool_name": "Grep", "tool_call_id": "tool_f",
    "tool_input": {"pattern": "def", "path": ".", "-n": True, "output_mode": "content"},
    "tool_response": {"output": "10:def f"},
}
_AGENT = {
    "hook_event_name": "PostToolUse", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "tool_name": "Agent", "tool_call_id": "tool_g",
    "tool_input": {"description": "x", "prompt": "go", "subagent_type": "explore",
                   "run_in_background": False},
    "tool_response": {"output": "summary"},
}
_FETCHURL = {
    "hook_event_name": "PostToolUse", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "tool_name": "FetchURL", "tool_call_id": "tool_h",
    "tool_input": {"url": "https://example.com"},
    "tool_response": {"output": "<html>"},
}
_TODOLIST = {
    "hook_event_name": "PostToolUse", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "tool_name": "TodoList", "tool_call_id": "tool_i",
    "tool_input": {"todos": [{"content": "do x", "status": "pending"}]},
    "tool_response": {"output": "ok"},
}

# ── hook-event payloads, in Kimi's native shape ──────────────────────

_PRE_TOOL_USE = {
    "hook_event_name": "PreToolUse", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "tool_name": "Read", "tool_call_id": "tool_j",
    "tool_input": {"path": "README.md"},
}
_USER_PROMPT_SUBMIT = {
    "hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi",
    "prompt": [{"type": "text", "text": "give me the arch of current project"}],
}
_POST_TOOL_FAILURE = {
    "hook_event_name": "PostToolUseFailure", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "tool_name": "Bash", "tool_call_id": "tool_k",
    "tool_input": {"command": "ls"},
    "error": {"code": "internal", "message": "boom", "retryable": False},
}
_SUBAGENT_START = {
    "hook_event_name": "SubagentStart", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "agent_name": "explore", "prompt": "Explore the repo",
}
_SUBAGENT_STOP = {
    "hook_event_name": "SubagentStop", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "agent_name": "explore", "response": "# summary",
}
_PRE_COMPACT = {
    "hook_event_name": "PreCompact", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "trigger": "auto", "token_count": 197224,
}
_POST_COMPACT = {
    "hook_event_name": "PostCompact", "session_id": "s1", "cwd": "/r",
    "agent_type": "kimi", "trigger": "auto", "estimated_token_count": 4593,
}

_ALL_KIMI_PAYLOADS = [
    _BASH, _READ, _EDIT, _WRITE, _GLOB, _GREP, _AGENT, _FETCHURL, _TODOLIST,
    _PRE_TOOL_USE, _USER_PROMPT_SUBMIT, _POST_TOOL_FAILURE,
    _SUBAGENT_START, _SUBAGENT_STOP, _PRE_COMPACT, _POST_COMPACT,
]


@pytest.mark.parametrize(
    "payload",
    _ALL_KIMI_PAYLOADS,
    ids=lambda p: f"{p['hook_event_name']}:{p.get('tool_name', '-')}",
)
def test_kimi_payload_validates_clean(payload):
    findings = _drift(payload)
    assert findings == [], [
        (f.drift_kind, f.field_path, f.actual_sample) for f in findings
    ]


def test_normalization_drops_redundant_kimi_aliases():
    """`_normalize_payload` canonicalizes Kimi's `tool_call_id`/`tool_output`
    onto `tool_use_id`/`tool_response` and drops the now-redundant originals,
    so the normalized payload (and its JSONL mirror) stays a single canonical
    shape rather than carrying both names."""
    norm = _normalize_payload(_BASH)
    assert "tool_call_id" not in norm
    assert "tool_output" not in norm
    assert norm["tool_use_id"] == "tool_a"
    assert norm["tool_response"] == {"output": "a\nb\n"}
