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
_SLOW_TOOLS = frozenset({'Bash', 'BashOutput', 'WebFetch', 'WebSearch'})


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
    from lib.hook_plugin import post_span  # type: ignore
    from lib.trace.pending_spans import tool_pending_id  # type: ignore

    attrs: dict = {'tool_name': tool, 'tool_use_id': tu_id, 'live': True}
    if tool == 'AskUserQuestion':
        questions = _ask_questions(payload.tool_input or {})
        if questions:
            attrs['questions'] = questions
    elif isinstance(payload.tool_input, dict) and payload.tool_input:
        # A small input preview so the pending card shows what's running
        # (e.g. the Bash command), mirroring the resolved card.
        attrs['tool_input'] = payload.tool_input
    post_span(
        trace_id=payload.session_id,
        name=f'tool.{tool}',
        span_id=tool_pending_id(tu_id),
        attributes=attrs,
        status_code='PENDING',
    )


def _ask_questions(tool_input: dict) -> list[dict]:
    """The question structure, mirroring `post_tool_trace._build_ask_attrs`
    (minus answers, which don't exist yet) so the pending card renders the
    same question the resolved card later will."""
    from .post_tool_trace import _ask_option  # type: ignore

    out: list[dict] = []
    for q in (tool_input.get('questions') or []):
        out.append({
            'question': q.get('question'),
            'header': q.get('header'),
            'options': [_ask_option(o) for o in (q.get('options') or [])],
            'multiSelect': q.get('multiSelect', False),
        })
    return out
