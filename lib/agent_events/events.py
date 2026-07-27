"""The producer-neutral event vocabulary.

regin captures a session two ways: by observing one the user drives (hooks +
transcript) and by owning one it launched itself (`lib/agent_sdk`). Both
converge here before anything is written, so the trace UI never learns there
are two sources.

Nothing provider- or transport-shaped may appear on these types. A Claude SDK
message or a Claude hook payload becomes an `AgentEvent` in its normalizer
(`from_sdk`, `from_hook`) and everything downstream sees only this vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Which interactive surface a permission request is asking for, mirroring the
# three shapes the /live card renders. Kept as a closed set: an unknown kind
# would reach the UI as an unanswerable card.
PERMISSION_KINDS = ('tool', 'question', 'plan')


@dataclass(frozen=True)
class AgentEvent:
    """Base for every event. `trace_id` is regin's session id."""

    trace_id: str


@dataclass(frozen=True)
class TurnStarted(AgentEvent):
    text: str = ''
    agent_id: str | None = None


@dataclass(frozen=True)
class TurnCompleted(AgentEvent):
    duration_ms: int = 0
    usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TurnFailed(AgentEvent):
    error: str = ''


@dataclass(frozen=True)
class ToolCall(AgentEvent):
    """A tool has started. Rendered in-flight until its `ToolResult` retires it."""

    tool_name: str = ''
    tool_use_id: str = ''
    tool_input: dict = field(default_factory=dict)
    agent_id: str | None = None
    agent_type: str | None = None


@dataclass(frozen=True)
class ToolResult(AgentEvent):
    tool_name: str = ''
    tool_use_id: str = ''
    output: object = None
    error: str | None = None
    duration_ms: int = 0
    agent_id: str | None = None
    agent_type: str | None = None


@dataclass(frozen=True)
class PermissionRequested(AgentEvent):
    """The agent is blocked waiting on the operator.

    For an SDK-owned session this is literally true — the tool call is parked
    inside the SDK until `PermissionResolved` carries an answer back. For a
    hook-observed session it is inferred, and nothing regin sends can unblock
    it except keystrokes.
    """

    tool_name: str = ''
    tool_use_id: str = ''
    tool_input: dict = field(default_factory=dict)
    kind: str = 'tool'


@dataclass(frozen=True)
class PermissionResolved(AgentEvent):
    tool_use_id: str = ''
    behavior: str = 'allow'
    detail: str = ''


@dataclass(frozen=True)
class UsageUpdated(AgentEvent):
    usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SubagentStarted(AgentEvent):
    agent_id: str = ''
    agent_type: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class SubagentStopped(AgentEvent):
    agent_id: str = ''
    duration_ms: int = 0
