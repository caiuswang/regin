"""Normalize Claude Agent SDK messages into neutral `AgentEvent`s.

This is the only module that knows the SDK's message shapes. It imports the
SDK's types lazily so importing `lib.agent_events` stays free for installs that
never launch an agent.

`parent_tool_use_id` is the subagent marker: a message carrying one was
produced inside a Task/Agent tool call, so its rows belong to that subagent
rather than the main agent.
"""

from __future__ import annotations

from .events import (
    AgentEvent,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)


def _block_type(block) -> str:
    return type(block).__name__


def _assistant_events(trace_id: str, message) -> list[AgentEvent]:
    agent_id = getattr(message, 'parent_tool_use_id', None)
    out: list[AgentEvent] = []
    for block in getattr(message, 'content', None) or []:
        if _block_type(block) != 'ToolUseBlock':
            continue
        out.append(ToolCall(
            trace_id=trace_id,
            tool_name=getattr(block, 'name', '') or '',
            tool_use_id=getattr(block, 'id', '') or '',
            tool_input=getattr(block, 'input', None) or {},
            agent_id=agent_id,
        ))
    return out


def _tool_result_error(block) -> str | None:
    if not getattr(block, 'is_error', False):
        return None
    content = getattr(block, 'content', None)
    return str(content) if content else 'tool failed'


def _user_events(trace_id: str, message) -> list[AgentEvent]:
    """Tool results echoed back to the model.

    A `UserMessage` whose content is plain text is the SDK echoing the prompt
    regin itself pushed, so it is dropped here — the runner emits the canonical
    `TurnStarted` at submit time rather than waiting for the echo.
    """
    agent_id = getattr(message, 'parent_tool_use_id', None)
    out: list[AgentEvent] = []
    content = getattr(message, 'content', None)
    if not isinstance(content, list):
        return out
    for block in content:
        if _block_type(block) != 'ToolResultBlock':
            continue
        out.append(ToolResult(
            trace_id=trace_id,
            tool_use_id=getattr(block, 'tool_use_id', '') or '',
            output=getattr(block, 'content', None),
            error=_tool_result_error(block),
            agent_id=agent_id,
        ))
    return out


def _result_events(trace_id: str, message) -> list[AgentEvent]:
    if getattr(message, 'is_error', False):
        return [TurnFailed(
            trace_id=trace_id,
            error=str(getattr(message, 'result', None) or 'turn failed'),
        )]
    return [TurnCompleted(
        trace_id=trace_id,
        duration_ms=int(getattr(message, 'duration_ms', 0) or 0),
        usage=dict(getattr(message, 'usage', None) or {}),
    )]


_HANDLERS = {
    'AssistantMessage': _assistant_events,
    'UserMessage': _user_events,
    'ResultMessage': _result_events,
}


def from_sdk_message(trace_id: str, message) -> list[AgentEvent]:
    """Neutral events for one SDK message; empty for messages regin ignores."""
    handler = _HANDLERS.get(type(message).__name__)
    return handler(trace_id, message) if handler else []


def prompt_event(trace_id: str, text: str) -> TurnStarted:
    """The canonical user-message row for a prompt regin submitted.

    Emitted at submit time rather than derived from the SDK's echo, so the row
    exists the moment the operator sends it — the same rule paseo follows for
    every provider adapter.
    """
    return TurnStarted(trace_id=trace_id, text=text)
