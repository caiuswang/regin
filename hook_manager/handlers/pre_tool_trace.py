"""Handler: PreToolUse → live "pending" span so an in-flight tool is visible.

A tool's resolved `tool.<Name>` span lands only at PostToolUse — *after* the
tool returns — so while it runs the trace shows nothing. Emit a PENDING
`tool.<Name>` span here so the operation is visible while it executes;
`ingest_session_spans` keeps it and the serve-time merge retires it (by
`tool_use_id`) the moment the resolved span for the same call arrives.

Scope: tools that can genuinely take a while —
  * blocking interactive tools (`AskUserQuestion`, `ExitPlanMode`) that wait
    on the user, and
  * long-running tools (`Bash`, web fetches/searches, any MCP tool).
Instant tools (Read/Edit/Grep/Glob/Write/…) are excluded: they resolve well
within a poll cycle, so a pending card would only flicker and would double
their ingest volume for no benefit. A long tool outlives the ~4 s poll, so its
pending card shows; a fast Bash retires before the next poll sees it.
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse

# Tools that block on the user before resolving.
_BLOCKING_TOOLS = frozenset({'AskUserQuestion', 'ExitPlanMode'})
# Non-blocking tools that can run long enough to be worth showing in-flight.
# `Agent` is here for its launch metadata as much as visibility: the resolved
# `tool.Agent` span (subagent_type / description / prompt) lands only after
# the subagent finishes, so without a pending twin the live subagent row has
# no goal to show (useAgentLaunchMerge pairs it with `subagent.start`).
_SLOW_TOOLS = frozenset({'Agent', 'Bash', 'BashOutput', 'WebFetch', 'WebSearch'})


def _should_emit_pending(tool: str) -> bool:
    return (tool in _BLOCKING_TOOLS or tool in _SLOW_TOOLS
            or tool.startswith('mcp__'))


def handle(payload: HookPayload) -> HookResponse | None:
    tool = payload.tool_name
    if not tool or not _should_emit_pending(tool):
        return None
    # Workflow-tool subagents fire PreToolUse into the launching session; their
    # in-flight cards belong to the run's own wf_ session, not here (see
    # HookPayload.is_workflow_subagent). Skip so they don't leave stale PENDING
    # placeholders flooding the launching conversation.
    if payload.is_workflow_subagent:
        return None
    tu_id = (payload.raw or {}).get('tool_use_id')
    if not isinstance(tu_id, str) or not tu_id:
        return None
    try:
        _emit_pending(payload, tool, tu_id)
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def _emit_pending(payload: HookPayload, tool: str, tu_id: str) -> None:
    """Project through the neutral event union so this row is byte-identical to
    the one an SDK-owned session emits for the same call (`lib/agent_events`).
    The union fixes name/span_id/status; the tool-specific attrs below are this
    producer's own enrichment."""
    from lib.agent_events import ToolCall, to_span  # type: ignore
    from lib.hook_plugin import post_span  # type: ignore

    span = to_span(ToolCall(
        trace_id=payload.session_id,
        tool_name=tool,
        tool_use_id=tu_id,
        agent_id=(payload.raw or {}).get('agent_id'),
        agent_type=(payload.raw or {}).get('agent_type'),
    ))
    _enrich_attrs(span['attributes'], payload, tool)
    post_span(**span)


def _enrich_attrs(attrs: dict, payload: HookPayload, tool: str) -> None:
    """Tool-specific presentation attrs on top of the union's identity fields."""
    if tool == 'AskUserQuestion':
        questions = _ask_questions(payload.tool_input or {})
        if questions:
            attrs['questions'] = questions
        return
    if tool == 'Agent':
        # Same structured launch attrs the resolved span gets, so the
        # subagent-row merge works identically against the pending twin.
        from .post_tool_trace import _build_agent_attrs  # type: ignore
        _build_agent_attrs(attrs, payload.tool_input or {}, {}, payload)
        return
    # Reuse the resolved card's input-derived attrs (Bash command, WebSearch
    # query, WebFetch url, …) so the in-flight card shows what's running
    # instead of a bare tool name — the conversation labellers read those
    # flat keys, not a raw `tool_input` dump.
    from .post_tool_trace import apply_pending_input_attrs  # type: ignore
    ti = payload.tool_input or {}
    if not apply_pending_input_attrs(attrs, tool, ti) and isinstance(ti, dict) and ti:
        # No tool-specific flat attrs (BashOutput, MCP, …) — keep a raw
        # input preview so the detail panel still shows the call.
        attrs['tool_input'] = ti


def _ask_questions(tool_input: dict) -> list[dict]:
    """The question structure the pending card renders.

    Shared with the SDK producer (`lib/agent_events/ask.py`) so both tiers
    describe an ask identically — the `/live` answer sheet reads this shape and
    refuses to offer options without it.
    """
    from lib.agent_events.ask import ask_questions  # type: ignore

    return ask_questions(tool_input)
