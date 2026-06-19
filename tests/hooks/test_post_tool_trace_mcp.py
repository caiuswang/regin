"""Regression: `tool.mcp__*` spans must carry the params asked and the result
returned, so the trace-detail panel can show the round-trip.

The bug: `_build_mcp_attrs` recorded only `mcp:True` (+ `user_message` for
send_to_user), so a memory-recall (or any MCP) span was opaque in the trace —
you couldn't tell whether the hits related to the query. The fix captures a
truncated `mcp_input` (params JSON) and `mcp_result` (response text).
"""

from __future__ import annotations

import json

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import post_tool_trace
from lib import hook_plugin


@pytest.fixture
def _captured(monkeypatch):
    spans: list[dict] = []
    monkeypatch.setattr(hook_plugin, 'post_span', lambda **kw: spans.append(kw))
    return spans


def _mcp_payload(tool_name, tool_input, tool_response):
    return HookPayload.from_stdin_json("PostToolUse", {
        "hook_event_name": "PostToolUse",
        "session_id": "session_mcp_1",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": "toolu_mcp_1",
        "tool_response": tool_response,
    })


def test_mcp_span_captures_params_and_result_dict(_captured):
    payload = _mcp_payload(
        "mcp__memory__recall",
        {"query": "schema drift alembic", "top_k": 3, "intent": "debug"},
        {"result": "[lesson|repo:regin|score 0.9] schema drift gotcha …"},
    )
    post_tool_trace._emit_span(payload)

    assert len(_captured) == 1
    attrs = _captured[0]["attributes"]
    assert _captured[0]["name"] == "tool.mcp__memory__recall"
    assert attrs["mcp"] is True
    assert attrs["tool_input_keys"] == ["query", "top_k", "intent"]
    # Params captured as JSON so the reader sees exactly what was asked.
    assert "schema drift alembic" in attrs["mcp_input"]
    assert json.loads(attrs["mcp_input"])["top_k"] == 3
    # Result extracted from the `{result: …}` StructuredOutput envelope.
    assert "schema drift gotcha" in attrs["mcp_result"]


def test_mcp_span_extracts_content_block_text(_captured):
    payload = _mcp_payload(
        "mcp__server__lookup",
        {"q": "x"},
        {"content": [
            {"type": "text", "text": "first block"},
            {"type": "text", "text": "second block"},
        ]},
    )
    post_tool_trace._emit_span(payload)

    attrs = _captured[0]["attributes"]
    assert attrs["mcp_result"] == "first block\nsecond block"


def test_mcp_span_handles_bare_string_result(_captured):
    payload = _mcp_payload("mcp__server__ping", {"a": 1}, "pong")
    post_tool_trace._emit_span(payload)

    assert _captured[0]["attributes"]["mcp_result"] == "pong"


def test_mcp_result_truncates_and_marks_dropped(_captured):
    big = "z" * (post_tool_trace._MCP_RESULT_MAX + 500)
    payload = _mcp_payload("mcp__server__big", {"a": 1}, big)
    post_tool_trace._emit_span(payload)

    attrs = _captured[0]["attributes"]
    assert len(attrs["mcp_result"]) == post_tool_trace._MCP_RESULT_MAX
    assert attrs["mcp_result_truncated_bytes"] == 500


def test_send_to_user_keeps_message_skips_generic_dump(_captured):
    # send_to_user has its own Messages-tab affordance; it must NOT also get
    # the generic params/result dump (the body is already in `user_message`).
    payload = _mcp_payload(
        "mcp__send-to-user__send_to_user",
        {"message": "hello world", "type": "progress"},
        {"result": "delivered"},
    )
    post_tool_trace._emit_span(payload)

    attrs = _captured[0]["attributes"]
    assert attrs["user_message"] == "hello world"
    assert "mcp_input" not in attrs
    assert "mcp_result" not in attrs
