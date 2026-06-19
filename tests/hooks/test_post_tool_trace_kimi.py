"""Regression: Kimi `tool.Bash`/`tool.Read` spans must carry their output.

Kimi returns every tool result as a single `{output, isError}` text envelope,
where regin's shared `post_tool_trace` builders read Claude's tool-specific
keys — `stdout`/`stderr` for Bash, `file.content` for Read. Without a provider
reshape both cards rendered an empty body (the reported bug: "Bash tool
invoking should have output, but currently nothing showed"). `KimiProvider.
normalize_tool_response` maps the envelope onto the keys the builders read, so
the handler stays provider-neutral. Claude payloads must still pass through.
"""

from __future__ import annotations

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import post_tool_trace
from lib import hook_plugin


@pytest.fixture
def _captured(monkeypatch):
    spans: list[dict] = []
    monkeypatch.setattr(hook_plugin, 'post_span', lambda **kw: spans.append(kw))
    return spans


def _kimi_payload(tool_name, tool_input, output):
    return HookPayload.from_stdin_json("PostToolUse", {
        "hook_event_name": "PostToolUse",
        "agent_type": "kimi",
        "session_id": "session_kimi_out",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_call_id": "tool_out_1",
        "tool_use_id": "tool_out_1",
        # Kimi's native shape: the result is a `{output}` envelope, *not*
        # Claude's per-tool keys.
        "tool_response": {"output": output},
    })


def test_kimi_bash_span_carries_stdout(_captured):
    payload = _kimi_payload("Bash", {"command": "echo hi"}, "hi\nthere\n")
    post_tool_trace._emit_span(payload)

    assert len(_captured) == 1
    attrs = _captured[0]["attributes"]
    assert _captured[0]["name"] == "tool.Bash"
    assert attrs["stdout"] == "hi\nthere\n"
    assert attrs["command_preview"] == "echo hi"


def test_kimi_read_span_carries_content_and_total_lines(_captured):
    output = (
        "1\thello kimi from regin test\n"
        "<system>1 line read from file starting from line 1. "
        "Total lines in file: 1. End of file reached.</system>"
    )
    payload = _kimi_payload("Read", {"path": "x.txt"}, output)
    post_tool_trace._emit_span(payload)

    assert len(_captured) == 1
    attrs = _captured[0]["attributes"]
    assert _captured[0]["name"] == "tool.Read"
    # The line-numbered body is kept; the `<system>` footer is stripped.
    assert attrs["content"] == "1\thello kimi from regin test"
    assert "<system>" not in attrs["content"]
    assert attrs["total_lines"] == 1


def test_kimi_edit_passes_through_diff_from_input(_captured):
    # Edit derives its diff from tool_input, so the `{output}` envelope needs
    # no mapping — the span must still render a diff.
    payload = _kimi_payload(
        "Edit",
        {"file_path": "f.py", "old_string": "a = 1", "new_string": "a = 2"},
        "ok",
    )
    post_tool_trace._emit_span(payload)

    assert len(_captured) == 1
    attrs = _captured[0]["attributes"]
    assert attrs["diff"]
    assert "stdout" not in attrs


def test_claude_bash_response_unchanged(_captured):
    # A Claude Bash payload already uses `stdout`/`stderr`; the provider must
    # not touch it (no `output` envelope to map).
    payload = HookPayload.from_stdin_json("PostToolUse", {
        "hook_event_name": "PostToolUse",
        "session_id": "session_claude_1",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_use_id": "toolu_claude_1",
        "tool_response": {"stdout": "file.txt\n", "stderr": ""},
    })
    post_tool_trace._emit_span(payload)

    assert len(_captured) == 1
    attrs = _captured[0]["attributes"]
    assert attrs["stdout"] == "file.txt\n"
