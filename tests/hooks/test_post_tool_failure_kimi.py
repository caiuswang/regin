"""Regression: Kimi PostToolUseFailure must emit a resolved failure span.

Kimi's CLI sends `error` as a structured object (`{code, message, retryable}`)
rather than Claude's bare string. The shared `post_tool_failure` handler used
to call `error.strip()` unconditionally, crashing with
``'dict' object has no attribute 'strip'`` — so no `tool.failure` span was
posted, the PreToolUse PENDING placeholder for the failed call was never
resolved, and the serve-time merge dropped it. Net effect: a failed Kimi tool
call vanished from the trace entirely. The provider now normalizes the error
shape (`tool_failure_error_text`), so the handler stays provider-neutral.
"""

from __future__ import annotations

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import post_tool_failure
from lib import hook_plugin


@pytest.fixture
def _captured(monkeypatch):
    spans: list[dict] = []
    monkeypatch.setattr(hook_plugin, 'post_span', lambda **kw: spans.append(kw))
    return spans


def _kimi_failure_payload():
    return HookPayload.from_stdin_json("PostToolUseFailure", {
        "hook_event_name": "PostToolUseFailure",
        "agent_type": "kimi",
        "session_id": "session_kimi_1",
        "tool_name": "Bash",
        "tool_input": {"command": "ls /nonexistent-xyz"},
        "tool_call_id": "tool_abc123def456",
        "error": {
            "code": "internal",
            "message": "ls: /nonexistent-xyz: No such file or directory\n"
                       "Command failed with exit code: 1.",
            "retryable": False,
        },
        "tool_use_id": "tool_abc123def456",
    })


def test_kimi_dict_error_emits_failure_span(_captured):
    resp = post_tool_failure.handle(_kimi_failure_payload())

    # Exactly one ERROR span, carrying the human message + the tool_use_id that
    # lets the serve-time merge reconcile away the PENDING placeholder.
    assert len(_captured) == 1
    span = _captured[0]
    assert span["name"] == "tool.failure"
    assert span["status_code"] == "ERROR"
    attrs = span["attributes"]
    assert attrs["tool_use_id"] == "tool_abc123def456"
    assert "No such file or directory" in attrs["error"]
    assert attrs["command_preview"] == "ls /nonexistent-xyz"

    # The model-facing context surfaces the message, not a stringified dict.
    assert resp is not None
    assert "No such file or directory" in (resp.additional_context or "")
    assert "'dict'" not in (resp.additional_context or "")


def test_kimi_user_rejection_emits_no_failure_span(_captured):
    # A rejected permission prompt arrives as a PostToolUseFailure whose
    # message says the user rejected it. The denial is captured as the
    # transcript `tooldeny-*` span, so the handler must NOT also emit a
    # `tool.failure` (which would double-render the one rejected call), and
    # must not re-inject a tool-failure context the agent already received.
    payload = HookPayload.from_stdin_json("PostToolUseFailure", {
        "hook_event_name": "PostToolUseFailure",
        "agent_type": "kimi",
        "session_id": "session_kimi_deny",
        "tool_name": "Bash",
        "tool_input": {"command": "echo DENY_ME"},
        "tool_use_id": "tool_rej1",
        "error": {
            "code": "internal",
            "message": 'Tool "Bash" was not run because the user rejected the approval request.',
            "retryable": False,
        },
    })
    resp = post_tool_failure.handle(payload)
    assert _captured == []  # no span emitted
    assert resp is not None
    assert not (resp.additional_context or "")  # no redundant context


def test_claude_string_error_still_emits_failure_span(_captured):
    payload = HookPayload.from_stdin_json("PostToolUseFailure", {
        "hook_event_name": "PostToolUseFailure",
        "session_id": "sess_claude_1",
        "tool_name": "Bash",
        "tool_input": {"command": "false"},
        "tool_use_id": "toolu_99",
        "error": "Command failed with exit code 1",
    })

    post_tool_failure.handle(payload)
    assert len(_captured) == 1
    assert _captured[0]["attributes"]["error"] == "Command failed with exit code 1"
