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
    """`usage` is already in regin's neutral token vocabulary — a producer
    normalizes the provider's own key names before constructing this."""

    duration_ms: int = 0
    usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AssistantText(AgentEvent):
    """What the agent said. The counterpart to `ToolCall`'s what it did.

    `turn_uuid` is the producer's turn identity — the value that joins this row
    to its `turn_usage` row and groups a turn's spans for the serve-time ladder.

    `message_id` is the API's own `msg_…`, and unlike `turn_uuid` it is the
    SAME value across producers — the join that lets the two traces of one
    SDK-launched session collapse to one.
    """

    text: str = ''
    model: str | None = None
    agent_id: str | None = None
    turn_uuid: str = ''
    turn_index: int = -1
    message_id: str = ''


@dataclass(frozen=True)
class AssistantThinking(AgentEvent):
    """Extended reasoning. `signature_bytes` is non-zero for encrypted
    thinking, where the reasoning happened but no text is readable."""

    text: str = ''
    signature_bytes: int = 0
    model: str | None = None
    agent_id: str | None = None
    turn_uuid: str = ''
    turn_index: int = -1
    message_id: str = ''


@dataclass(frozen=True)
class SessionStarted(AgentEvent):
    source: str = 'startup'
    model: str | None = None
    cwd: str | None = None
    agent_type: str | None = None
    # The session this one continues (`--resume`). A resumed run gets its own
    # trace, so without this the conversation it inherits is invisible.
    resumed_from: str | None = None


@dataclass(frozen=True)
class SessionEnded(AgentEvent):
    reason: str = 'exited'


@dataclass(frozen=True)
class TurnFailed(AgentEvent):
    """A turn that ended badly — interrupted, or errored out.

    It still carries `usage`: the tokens were spent whether or not the turn
    produced an answer, and an interrupt is the *most* likely way for a turn to
    end, so dropping its spend would bias cost and the context meter downward
    on exactly the path an operator uses most.
    """

    error: str = ''
    usage: dict = field(default_factory=dict)


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
    """How a `PermissionRequested` ended. `tool_name` rides along because the
    resolution retires the request's placeholder row, and a denial that names
    no tool is the only surviving record of what was refused."""

    tool_use_id: str = ''
    tool_name: str = ''
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
