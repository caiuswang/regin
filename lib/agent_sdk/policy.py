"""Which tool calls an SDK-owned session parks for the operator, and how a
parked one is described to `/live`.

Owning the process means `can_use_tool` can hold **any** tool call open until a
human decides it — the capability the tmux tier structurally cannot have, where
"deciding" is keystrokes aimed at whatever widget is on screen. What that
capability must not do is change an install that never asked for it, so the
default here is exactly what the tier already did: only `AskUserQuestion`
parks. Plan review (`agent_sdk.gate_plan`) and tool gating
(`agent_sdk.gated_tools`) are opt-in.

The preview phrasing mirrors the hook tier's (`lib/providers/claude`
`_requested_permission`, `hook_manager/handlers/permission_events`) rather than
inventing a second vocabulary: the `/live` card reads `command_preview` then
`requested_permission` whichever producer wrote the span.
"""

from __future__ import annotations

from lib.settings import settings

QUESTION_TOOL = 'AskUserQuestion'
PLAN_TOOL = 'ExitPlanMode'

# `gated_tools` entry that gates every tool call — "approve everything from my
# phone", which naming each tool cannot express for tools regin can't enumerate
# (MCP tools arrive with server-specific names).
GATE_ALL = '*'

_PREVIEW_MAX = 500
_COMMAND_PREVIEW_MAX = 200
_PLAN_MAX = 8000
_PATH_TOOLS = frozenset({'Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit'})

# A park has no clock, so this is the teardown reading and nothing else: the
# session went away under an operator who never answered. Nothing may report a
# refusal the human did not make — a timer that declined an unseen question
# had the agent narrating "you dismissed this" to someone who never saw it.
DISMISSED = 'Dismissed by operator'
_DENIED = 'Denied by operator'


def permission_kind(tool_name: str) -> str | None:
    """The `PERMISSION_KINDS` value this call parks under, or None to allow it.

    `AskUserQuestion` is unconditional: it blocks on a human by its own nature,
    and auto-allowing it would run the tool with no answers at all.
    """
    if tool_name == QUESTION_TOOL:
        return 'question'
    cfg = settings.agent_sdk
    if tool_name == PLAN_TOOL and cfg.gate_plan:
        return 'plan'
    gated = cfg.gated_tools or []
    if GATE_ALL in gated or tool_name in gated:
        return 'tool'
    return None


def _text_field(tool_input: dict, *names: str) -> str:
    for name in names:
        value = (tool_input or {}).get(name)
        if isinstance(value, str) and value:
            return value
    return ''


def _bash_preview(tool_input: dict) -> str:
    command = _text_field(tool_input, 'command')
    return f'Run shell command: {command[:_PREVIEW_MAX]}' if command else ''


def _path_preview(tool_name: str, tool_input: dict) -> str:
    path = _text_field(tool_input, 'file_path', 'path')
    verb = 'Read' if tool_name == 'Read' else 'Modify'
    return f'{verb} file: {path[:_PREVIEW_MAX]}' if path else ''


def _requested_permission(tool_name: str, tool_input: dict) -> str:
    fallback = f'Use tool: {tool_name or "unknown"}'
    if tool_name == PLAN_TOOL:
        return 'Approve the plan and start building'
    if tool_name == 'Bash':
        return _bash_preview(tool_input) or fallback
    if tool_name in _PATH_TOOLS:
        return _path_preview(tool_name, tool_input) or fallback
    return fallback


def request_attrs(tool_name: str, tool_input: dict, kind: str) -> dict:
    """Presentation attrs for a parked call's `permission.request` span.

    Without these the card can only name the tool, and an operator approving
    from a phone would be deciding on `tool.Bash` with no command in sight.
    """
    attrs = {'requested_permission': _requested_permission(tool_name, tool_input)}
    command = _text_field(tool_input, 'command')
    if tool_name == 'Bash' and command:
        attrs['command_preview'] = command[:_COMMAND_PREVIEW_MAX]
    plan = _text_field(tool_input, 'plan')
    # Keyed on the tool, not the kind it parked under: `gated_tools = ["*"]`
    # holds `ExitPlanMode` as a plain gated tool, and without the text the
    # operator is asked to approve a plan they cannot read.
    if tool_name == PLAN_TOOL and plan:
        attrs['plan'] = plan[:_PLAN_MAX]
    return attrs


def notify_attrs(kind: str, tool_name: str, tool_input: dict,
                 tool_use_id: str) -> dict:
    """The attrs `event_notify.notify_permission_request` formats into a card.

    Deliberately the same vocabulary the hook tier's `_build_perm_attrs`
    produces — one inbox card shape has to render a park from either producer,
    or the phone would show two different notions of "waiting on you".
    """
    attrs = {'tool_name': tool_name, 'tool_use_id': tool_use_id}
    if kind == 'question':
        questions = (tool_input or {}).get('questions')
        if isinstance(questions, list) and questions:
            attrs['questions'] = questions
        return attrs
    attrs.update(request_attrs(tool_name, tool_input, kind))
    return attrs


def decision_outcome(decision) -> tuple[str, str]:
    """(behavior, detail) for a resolved permission request.

    A `None` decision is a dismissal — the session was torn down under an
    operator who never answered — and denying is the only safe reading: the
    alternative runs a gated tool nobody approved.
    """
    if not isinstance(decision, dict):
        return 'deny', DISMISSED
    behavior = 'allow' if decision.get('behavior') == 'allow' else 'deny'
    reason = decision.get('reason')
    detail = reason.strip()[:_PREVIEW_MAX] if isinstance(reason, str) else ''
    if not detail and behavior == 'deny':
        detail = _DENIED
    return behavior, detail
