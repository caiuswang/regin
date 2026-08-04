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
class TextBlock:
    text: str


@dataclass
class UserMessage:
    content: object
    parent_tool_use_id: str | None = None
    uuid: str | None = None


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


def test_echo_without_a_uuid_is_dropped():
    """No uuid means no identity to join on; the submit-time placeholder
    already shows the prompt."""
    assert from_sdk_message("t1", UserMessage(content="hello there")) == []


def test_echo_with_a_uuid_becomes_the_resolved_prompt():
    from lib.agent_events import PromptDelivered

    events = from_sdk_message(
        "t1", UserMessage(content="hello there", uuid="3771b684-27fe-425d"))

    assert events == [PromptDelivered(trace_id="t1", text="hello there",
                                      entry_uuid="3771b684-27fe-425d")]


def test_text_block_echo_also_carries_the_uuid():
    from lib.agent_events import PromptDelivered

    events = from_sdk_message(
        "t1", UserMessage(content=[TextBlock(text="steer msg")], uuid="u-1"))

    assert events == [PromptDelivered(trace_id="t1", text="steer msg",
                                      entry_uuid="u-1")]


def test_subagent_echo_never_becomes_a_top_level_prompt():
    assert from_sdk_message(
        "t1", UserMessage(content="task prompt", uuid="u-2",
                          parent_tool_use_id="toolu_9")) == []


def test_prompt_delivered_projects_to_the_anchor_span_id():
    from lib.agent_events import PromptDelivered
    from lib.trace.pending_spans import prompt_placeholder_id

    span = to_span(PromptDelivered(trace_id="t1", text="hello there",
                                   entry_uuid="3771b684-27fe-425d-a0ee"))

    assert span["span_id"] == "prompt-3771b684-27fe"
    assert span["status_code"] == "OK"
    assert span["attributes"]["entry_uuid"] == "3771b684-27fe-425d-a0ee"
    assert span["attributes"]["pending_span_id"] == \
        prompt_placeholder_id("t1", "hello there")


def test_turn_started_projects_to_a_pending_placeholder():
    from lib.agent_events import TurnStarted
    from lib.trace.pending_spans import prompt_placeholder_id

    span = to_span(TurnStarted(trace_id="t1", text="hello there"))

    assert span["status_code"] == "PENDING"
    assert span["span_id"] == prompt_placeholder_id("t1", "hello there")
    assert span["attributes"]["live_placeholder"] is True


def test_agent_scoped_turn_started_stays_a_resolved_row():
    from lib.agent_events import TurnStarted

    span = to_span(TurnStarted(trace_id="t1", text="sub prompt",
                               agent_id="toolu_5"))

    assert span["status_code"] == "OK"
    assert "span_id" not in span or not str(
        span.get("span_id", "")).startswith("promptlive-")


def test_result_message_splits_on_is_error():
    ok = from_sdk_message("t1", ResultMessage(duration_ms=12))
    bad = from_sdk_message("t1", ResultMessage(is_error=True, result="died"))

    assert isinstance(ok[0], TurnCompleted)
    assert ok[0].duration_ms == 12
    assert bad[0].error == "died"


def test_unknown_sdk_message_yields_no_events():
    assert from_sdk_message("t1", object()) == []
