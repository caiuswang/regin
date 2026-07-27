"""The neutral event union and its projection (`lib/agent_events`).

The point of the union is that a row's identity does not depend on which
producer wrote it: a tool call observed through a hook and the same call
observed through the SDK must land on the same span name, span id and status,
because the serve-time merge joins them on exactly those fields.
"""

from __future__ import annotations

from dataclasses import dataclass

from lib.agent_events import (
    PermissionRequested,
    ToolCall,
    ToolResult,
    TurnCompleted,
    to_span,
)
from lib.agent_events.from_sdk import from_sdk_message
from lib.trace.pending_spans import perm_pending_id, tool_pending_id


# The SDK's own classes are matched by name, so local stand-ins exercise the
# normalizer without a live agent or the SDK installed.
@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: object = None
    is_error: bool = False


@dataclass
class AssistantMessage:
    content: list
    parent_tool_use_id: str | None = None


@dataclass
class UserMessage:
    content: list
    parent_tool_use_id: str | None = None


@dataclass
class ResultMessage:
    is_error: bool = False
    result: str | None = None
    duration_ms: int = 0
    usage: dict | None = None


def test_tool_call_projects_to_the_shared_pending_span_id():
    span = to_span(ToolCall(trace_id="t1", tool_name="Bash",
                            tool_use_id="toolu_abc123"))

    assert span["name"] == "tool.Bash"
    assert span["span_id"] == tool_pending_id("toolu_abc123")
    assert span["status_code"] == "PENDING"
    assert span["attributes"]["live"] is True


def test_permission_request_uses_the_permission_pending_prefix():
    span = to_span(PermissionRequested(trace_id="t1", tool_name="AskUserQuestion",
                                       tool_use_id="toolu_x", kind="question"))

    assert span["name"] == "permission.request"
    assert span["span_id"] == perm_pending_id("toolu_x")
    assert span["attributes"]["kind"] == "question"


def test_failed_tool_result_is_an_error_span():
    span = to_span(ToolResult(trace_id="t1", tool_name="Bash",
                              tool_use_id="toolu_x", error="boom"))

    assert span["status_code"] == "ERROR"
    assert span["attributes"]["error"] == "boom"


def test_agent_type_is_not_stamped_without_an_agent_id():
    span = to_span(ToolCall(trace_id="t1", tool_name="Read", tool_use_id="tu",
                            agent_type="explorer"))

    assert "agent_type" not in span["attributes"]
    assert "agent_id" not in span["attributes"]


def test_events_with_no_span_project_to_none():
    assert to_span(TurnCompleted(trace_id="t1")) is None


def test_assistant_tool_use_becomes_a_tool_call():
    message = AssistantMessage(
        content=[ToolUseBlock(id="toolu_1", name="Bash", input={"command": "ls"})])

    events = from_sdk_message("t1", message)

    assert len(events) == 1
    assert isinstance(events[0], ToolCall)
    assert events[0].tool_name == "Bash"
    assert events[0].tool_use_id == "toolu_1"


def test_parent_tool_use_id_marks_the_event_as_a_subagent_row():
    message = AssistantMessage(
        content=[ToolUseBlock(id="toolu_1", name="Read", input={})],
        parent_tool_use_id="toolu_parent")

    assert from_sdk_message("t1", message)[0].agent_id == "toolu_parent"


def test_user_tool_result_becomes_a_tool_result_carrying_the_error():
    message = UserMessage(
        content=[ToolResultBlock(tool_use_id="toolu_1", content="nope",
                                 is_error=True)])

    events = from_sdk_message("t1", message)

    assert isinstance(events[0], ToolResult)
    assert events[0].error == "nope"


def test_plain_text_user_message_is_dropped_as_the_prompt_echo():
    assert from_sdk_message("t1", UserMessage(content="hello there")) == []


def test_result_message_splits_on_is_error():
    ok = from_sdk_message("t1", ResultMessage(duration_ms=12))
    bad = from_sdk_message("t1", ResultMessage(is_error=True, result="died"))

    assert isinstance(ok[0], TurnCompleted)
    assert ok[0].duration_ms == 12
    assert bad[0].error == "died"


def test_unknown_sdk_message_yields_no_events():
    assert from_sdk_message("t1", object()) == []
