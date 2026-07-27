"""Project a neutral `AgentEvent` onto regin's span shape.

The returned dict is the keyword set `lib.hook_plugin.post_span` takes, so both
producers reach `session_spans` through one writer. Events that carry no span
(usage roll-ups) project to `None` and are routed to the usage ingest instead.

The union fixes a row's *identity* — name, span_id, status, the ids the
serve-time merge joins on. Tool-specific presentation attrs (a Bash command, a
WebSearch query) stay a producer concern: a caller may add to the returned
`attributes` before posting. Pulling that derivation in here would drag every
tool's formatting into the vocabulary without making any of it neutral.

Span ids for in-flight rows come from `lib.trace.pending_spans`, so a live row
emitted here is retired by the same serve-time merge that retires a
hook-emitted one — the merge matches on `tool_use_id` and never learns which
producer wrote the row.
"""

from __future__ import annotations

from .events import (
    AgentEvent,
    AssistantText,
    AssistantThinking,
    PermissionRequested,
    PermissionResolved,
    SessionEnded,
    SessionStarted,
    SubagentStarted,
    SubagentStopped,
    ToolCall,
    ToolResult,
    TurnFailed,
    TurnStarted,
)


def _agent_attrs(attrs: dict, event) -> None:
    """Stamp subagent identity so the row renders under the right agent.

    Without it every subagent-scoped row lands under the MAIN agent.
    """
    if not getattr(event, 'agent_id', None):
        return
    attrs['agent_id'] = event.agent_id
    if getattr(event, 'agent_type', None):
        attrs['agent_type'] = event.agent_type


def _tool_call_span(event: ToolCall) -> dict:
    from lib.trace.pending_spans import tool_pending_id

    attrs = {
        'tool_name': event.tool_name,
        'tool_use_id': event.tool_use_id,
        'live': True,
    }
    if event.tool_input:
        attrs['tool_input'] = event.tool_input
    _agent_attrs(attrs, event)
    return {
        'name': f'tool.{event.tool_name}',
        'span_id': tool_pending_id(event.tool_use_id),
        'attributes': attrs,
        'status_code': 'PENDING',
    }


def _tool_result_span(event: ToolResult) -> dict:
    attrs = {'tool_name': event.tool_name, 'tool_use_id': event.tool_use_id}
    if event.error:
        attrs['error'] = event.error
    _agent_attrs(attrs, event)
    return {
        'name': f'tool.{event.tool_name}',
        'attributes': attrs,
        'duration_ms': event.duration_ms,
        'status_code': 'ERROR' if event.error else 'OK',
    }


def _permission_requested_span(event: PermissionRequested) -> dict:
    from lib.trace.pending_spans import perm_pending_id

    attrs = {
        'tool_name': event.tool_name,
        'tool_use_id': event.tool_use_id,
        'kind': event.kind,
        'live': True,
    }
    if event.tool_input:
        attrs['tool_input'] = event.tool_input
    return {
        'name': 'permission.request',
        'span_id': perm_pending_id(event.tool_use_id),
        'attributes': attrs,
        'status_code': 'PENDING',
    }


def _permission_resolved_span(event: PermissionResolved) -> dict:
    attrs = {
        'tool_use_id': event.tool_use_id,
        'behavior': event.behavior,
        'detail': event.detail,
    }
    if event.tool_name:
        attrs['tool_name'] = event.tool_name
    if event.behavior != 'allow':
        # The card reads a denial's cause off `reason`, the key the hook tier's
        # permission spans carry; `detail` alone renders an unexplained ✗.
        attrs['reason'] = event.detail
    return {
        'name': ('permission.request' if event.behavior == 'allow'
                 else 'permission.denied'),
        'attributes': attrs,
        'status_code': 'OK' if event.behavior == 'allow' else 'ERROR',
    }


def _turn_started_span(event: TurnStarted) -> dict:
    attrs: dict = {'text': event.text}
    if event.agent_id:
        attrs['agent_id'] = event.agent_id
    return {'name': 'prompt', 'attributes': attrs, 'status_code': 'OK'}


def _cap_text(text: str, max_bytes: int) -> tuple[str, bool]:
    """Same byte cap and marker the hook producer applies, so a long response
    is truncated identically whichever producer captured it."""
    if max_bytes <= 0:
        return text, False
    encoded = text.encode('utf-8')
    if len(encoded) <= max_bytes:
        return text, False
    head = encoded[:max_bytes].decode('utf-8', errors='ignore')
    return head + '\n\n…[truncated]', True


def _text_tokens(text: str) -> int:
    from lib.tokens.token_estimator import estimate_text_tokens

    return estimate_text_tokens(text) if text else 0


def _capture_policy() -> tuple[bool, int]:
    from lib.settings import settings

    return (bool(settings.capture_assistant_response),
            int(settings.assistant_response_max_bytes or 0))


def _turn_attrs(attrs: dict, event) -> None:
    """The turn identity every hook-tier assistant span carries, so a row's
    turn is readable whichever producer wrote it."""
    if getattr(event, 'turn_uuid', ''):
        attrs['turn_uuid'] = event.turn_uuid
    if getattr(event, 'turn_index', -1) >= 0:
        attrs['turn_index'] = event.turn_index


def _assistant_text_span(event: AssistantText) -> dict | None:
    capture, max_bytes = _capture_policy()
    if not capture:
        return None
    text, truncated = _cap_text(event.text, max_bytes)
    attrs = {
        'text': text,
        'response_chars': len(event.text),
        'output_tokens': _text_tokens(event.text),
    }
    if truncated:
        attrs['truncated'] = True
    if event.model:
        attrs['model'] = event.model
    _turn_attrs(attrs, event)
    _agent_attrs(attrs, event)
    return {'name': 'assistant_response', 'attributes': attrs,
            'status_code': 'OK'}


def _assistant_thinking_span(event: AssistantThinking) -> dict | None:
    capture, max_bytes = _capture_policy()
    if not capture:
        return None
    text, truncated = _cap_text(event.text, max_bytes)
    attrs = {
        'thinking_blocks': 1,
        'thinking_signature_bytes': event.signature_bytes,
        'output_tokens': _text_tokens(event.text),
    }
    if text:
        attrs['thinking_text'] = text
        attrs['thinking_truncated'] = truncated
    if event.model:
        attrs['model'] = event.model
    _turn_attrs(attrs, event)
    _agent_attrs(attrs, event)
    return {'name': 'assistant.thinking', 'attributes': attrs,
            'status_code': 'OK'}


def _session_started_span(event: SessionStarted) -> dict:
    attrs = {'source': event.source}
    if event.model:
        attrs['model'] = event.model
    if event.cwd:
        attrs['cwd'] = event.cwd
    if event.agent_type:
        attrs['agent_type'] = event.agent_type
    return {'name': 'session.start', 'attributes': attrs, 'status_code': 'OK'}


def _session_ended_span(event: SessionEnded) -> dict:
    return {'name': 'session.end', 'attributes': {'reason': event.reason},
            'status_code': 'OK'}


def _turn_failed_span(event: TurnFailed) -> dict:
    return {
        'name': 'turn.cancel',
        'attributes': {'error': event.error},
        'status_code': 'ERROR',
    }


def _subagent_started_span(event: SubagentStarted) -> dict:
    attrs = {'agent_id': event.agent_id}
    if event.agent_type:
        attrs['agent_type'] = event.agent_type
    if event.description:
        attrs['description'] = event.description
    return {'name': 'subagent.start', 'attributes': attrs, 'status_code': 'OK'}


def _subagent_stopped_span(event: SubagentStopped) -> dict:
    return {
        'name': 'subagent.stop',
        'attributes': {'agent_id': event.agent_id},
        'duration_ms': event.duration_ms,
        'status_code': 'OK',
    }


_BUILDERS = {
    ToolCall: _tool_call_span,
    ToolResult: _tool_result_span,
    PermissionRequested: _permission_requested_span,
    PermissionResolved: _permission_resolved_span,
    TurnStarted: _turn_started_span,
    TurnFailed: _turn_failed_span,
    AssistantText: _assistant_text_span,
    AssistantThinking: _assistant_thinking_span,
    SessionStarted: _session_started_span,
    SessionEnded: _session_ended_span,
    SubagentStarted: _subagent_started_span,
    SubagentStopped: _subagent_stopped_span,
}


def to_span(event: AgentEvent) -> dict | None:
    """Keyword set for `post_span`, or None when the event carries no span.

    A builder may also return None when capture of that row is switched off
    (`capture_assistant_response`), which reads the same to every caller as an
    event that never had a span.
    """
    builder = _BUILDERS.get(type(event))
    if builder is None:
        return None
    span = builder(event)
    if span is None:
        return None
    span['trace_id'] = event.trace_id
    return span
