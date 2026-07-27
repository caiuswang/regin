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
    PermissionRequested,
    PermissionResolved,
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
    return {
        'name': ('permission.request' if event.behavior == 'allow'
                 else 'permission.denied'),
        'attributes': {
            'tool_use_id': event.tool_use_id,
            'behavior': event.behavior,
            'detail': event.detail,
        },
        'status_code': 'OK' if event.behavior == 'allow' else 'ERROR',
    }


def _turn_started_span(event: TurnStarted) -> dict:
    attrs: dict = {'text': event.text}
    if event.agent_id:
        attrs['agent_id'] = event.agent_id
    return {'name': 'prompt', 'attributes': attrs, 'status_code': 'OK'}


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
    SubagentStarted: _subagent_started_span,
    SubagentStopped: _subagent_stopped_span,
}


def to_span(event: AgentEvent) -> dict | None:
    """Keyword set for `post_span`, or None when the event carries no span."""
    builder = _BUILDERS.get(type(event))
    if builder is None:
        return None
    span = builder(event)
    span['trace_id'] = event.trace_id
    return span
