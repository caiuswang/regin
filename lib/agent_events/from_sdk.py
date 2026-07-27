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
    AssistantText,
    AssistantThinking,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    UsageUpdated,
)


def _block_type(block) -> str:
    return type(block).__name__


def _text_event(trace_id, block, agent_id, model) -> AgentEvent | None:
    text = getattr(block, 'text', '') or ''
    if not text.strip():
        return None
    return AssistantText(trace_id=trace_id, text=text, model=model,
                         agent_id=agent_id)


def _thinking_event(trace_id, block, agent_id, model) -> AgentEvent | None:
    text = getattr(block, 'thinking', '') or ''
    signature = getattr(block, 'signature', '') or ''
    if not text and not signature:
        return None
    return AssistantThinking(trace_id=trace_id, text=text,
                             signature_bytes=len(signature), model=model,
                             agent_id=agent_id)


def _tool_use_event(trace_id, block, agent_id, model) -> AgentEvent:
    return ToolCall(
        trace_id=trace_id,
        tool_name=getattr(block, 'name', '') or '',
        tool_use_id=getattr(block, 'id', '') or '',
        tool_input=getattr(block, 'input', None) or {},
        agent_id=agent_id,
    )


_BLOCK_EVENTS = {
    'TextBlock': _text_event,
    'ThinkingBlock': _thinking_event,
    'ToolUseBlock': _tool_use_event,
}


def _assistant_events(trace_id: str, message) -> list[AgentEvent]:
    """Every block of one assistant message, in the order the model emitted
    them — reasoning, then prose, then the calls they led to.

    A trailing `UsageUpdated` carries this **API call's** usage. Only its
    prompt-side counters are trustworthy: `output_tokens` here is a streaming
    partial (measured: 3 and 1 across a turn that really spent 288), which is
    why billing totals come from the turn's `ResultMessage` instead.
    """
    agent_id = getattr(message, 'parent_tool_use_id', None)
    model = getattr(message, 'model', None) or None
    out: list[AgentEvent] = []
    for block in getattr(message, 'content', None) or []:
        builder = _BLOCK_EVENTS.get(_block_type(block))
        event = builder(trace_id, block, agent_id, model) if builder else None
        if event is not None:
            out.append(event)
    # Emitted even when the message produced no events of its own: a call
    # carrying only server-tool blocks, or an empty one, still moved the
    # context, and skipping it would leave a stale earlier call standing in as
    # this turn's context size.
    usage = getattr(message, 'usage', None)
    if isinstance(usage, dict) and not agent_id:
        out.append(UsageUpdated(trace_id=trace_id,
                                usage=_neutral_usage(usage)))
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


def _int(value) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _neutral_usage(raw) -> dict:
    """The SDK's usage block in regin's token vocabulary.

    The API names the cache counters `cache_*_input_tokens`; every regin
    consumer (`turn_usage`, the context meter) reads `cache_*_tokens`, so the
    rename happens here rather than leaking a provider key downstream.
    """
    usage = raw if isinstance(raw, dict) else {}
    return {
        'input_tokens': _int(usage.get('input_tokens')),
        'output_tokens': _int(usage.get('output_tokens')),
        'cache_read_tokens': _int(usage.get('cache_read_input_tokens')
                                  or usage.get('cache_read_tokens')),
        'cache_creation_tokens': _int(usage.get('cache_creation_input_tokens')
                                      or usage.get('cache_creation_tokens')),
    }


def _result_events(trace_id: str, message) -> list[AgentEvent]:
    if getattr(message, 'is_error', False):
        return [TurnFailed(
            trace_id=trace_id,
            error=str(getattr(message, 'result', None) or 'turn failed'),
            usage=_neutral_usage(getattr(message, 'usage', None)),
        )]
    return [TurnCompleted(
        trace_id=trace_id,
        duration_ms=int(getattr(message, 'duration_ms', 0) or 0),
        usage=_neutral_usage(getattr(message, 'usage', None)),
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
