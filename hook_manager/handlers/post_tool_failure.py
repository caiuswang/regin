"""Handler: PostToolUseFailure — surface the error shape into the transcript.

Default behavior of Claude Code is to tell the model a tool failed with a
terse error. This handler tacks on structured context (tool_name, error
type/preview, whether it was a user interrupt) so the model can decide
whether to retry vs. give up.
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse
from . import post_tool_trace

# Cap the stored error string. Tracebacks rarely exceed a few KB, but a
# tool that wedges and dumps a giant log into the error field shouldn't
# bloat the span. 16 KB is well above any realistic Python traceback.
_ERROR_MAX = 16 * 1024
# The additional_context the model sees stays compact — a 200-char head
# is enough for it to decide retry vs. give up. The full error lives on
# the span for the UI.
_CONTEXT_PREVIEW_MAX = 200


def handle(payload: HookPayload) -> HookResponse | None:
    tool = payload.tool_name or 'unknown'
    err = (payload.raw.get('error') or '').strip()
    stored_err = err[:_ERROR_MAX]
    err_dropped = max(0, len(err) - len(stored_err))
    context_err = err[:_CONTEXT_PREVIEW_MAX] + ('…' if len(err) > _CONTEXT_PREVIEW_MAX else '')
    interrupt = bool(payload.raw.get('is_interrupt'))

    try:
        _emit_span(payload, tool, stored_err, err_dropped, interrupt)
    except Exception:
        pass

    bits = [f'tool-failure: {tool}']
    if interrupt:
        bits.append('(user interrupt)')
    if context_err:
        bits.append(f'error={context_err!r}')
    return HookResponse(
        suppress_output=True,
        additional_context=' '.join(bits),
    )


def _capture_bash(attrs: dict, tool_input: dict) -> None:
    """Carry the failed Bash command onto the span (preview always; full,
    truncated body only when it overflows) — mirrors post_tool_trace so a
    failure row renders with the same command context a success would."""
    cmd_full = (tool_input or {}).get('command') or ''
    if not (isinstance(cmd_full, str) and cmd_full):
        return
    attrs['command_preview'] = post_tool_trace._bash_preview(tool_input)
    if len(cmd_full) > post_tool_trace._PREVIEW_MAX:
        cmd_stored = cmd_full[:post_tool_trace._BASH_COMMAND_MAX]
        attrs['command'] = cmd_stored
        cmd_dropped = len(cmd_full) - len(cmd_stored)
        if cmd_dropped:
            attrs['command_truncated_bytes'] = cmd_dropped


def _tag_subagent(attrs: dict, raw: dict) -> None:
    """Persist the subagent's `agent_id` (+ optional `agent_type`) onto the
    failure span — exactly as the success path (post_tool_trace._emit_span)
    does. Without it a failed call made inside a subagent has no `agent_id`,
    so the trace projection can't re-parent it under the matching
    `subagent.start` and it renders adrift, flat under the main prompt."""
    agent_id = raw.get('agent_id')
    if not agent_id:
        return
    attrs['agent_id'] = agent_id
    agent_type = raw.get('agent_type')
    if agent_type:
        attrs['agent_type'] = agent_type


def _emit_span(payload: HookPayload, tool: str, error: str, error_dropped: int, interrupt: bool) -> None:
    from lib.hook_plugin import post_span  # type: ignore
    tool_input = payload.tool_input or {}
    attrs: dict = {'tool_name': tool, 'is_interrupt': interrupt}
    # Mirror post_tool_trace: preserve the Anthropic-assigned `toolu_…` id so
    # the failure span can be correlated with the assistant_response.tool_calls
    # entry (and so tool_attribution can later land tokens on it). Without
    # this the failure shows up as an orphan with parent_id=None and renders
    # outside its owning prompt until the read-time graft fires.
    tu_id = (payload.raw or {}).get('tool_use_id')
    if isinstance(tu_id, str) and tu_id:
        attrs['tool_use_id'] = tu_id
    if error:
        attrs['error'] = error
        if error_dropped:
            attrs['error_truncated_bytes'] = error_dropped
    # Capture the input that caused the failure so the trace UI can show
    # *what* failed, not just the error — mirrors post_tool_trace so a
    # failure span renders with the same command/file_path context a
    # successful one would have.
    fp = post_tool_trace._file_path(tool_input)
    if fp:
        attrs['file_path'] = fp
    if tool == 'Bash':
        _capture_bash(attrs, tool_input)
    _tag_subagent(attrs, payload.raw or {})
    post_span(
        trace_id=payload.session_id,
        name='tool.failure',
        attributes=attrs,
        status_code='ERROR',
    )
