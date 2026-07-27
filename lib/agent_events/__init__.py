"""Producer-neutral agent events — regin's single capture vocabulary.

Two producers converge here: the hook path (`hook_manager/handlers/`, observing
a session the user drives) and the SDK path (`lib/agent_sdk/`, driving a session
regin launched). Both project through `to_span` into one writer, so the trace UI
renders both without knowing which produced a row.
"""

from .events import (
    AgentEvent,
    PERMISSION_KINDS,
    PermissionRequested,
    PermissionResolved,
    SubagentStarted,
    SubagentStopped,
    ToolCall,
    ToolResult,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    UsageUpdated,
)
from .spans import to_span

__all__ = [
    'AgentEvent',
    'PERMISSION_KINDS',
    'PermissionRequested',
    'PermissionResolved',
    'SubagentStarted',
    'SubagentStopped',
    'ToolCall',
    'ToolResult',
    'TurnCompleted',
    'TurnFailed',
    'TurnStarted',
    'UsageUpdated',
    'to_span',
]
