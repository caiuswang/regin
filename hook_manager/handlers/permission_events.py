"""Handlers: PermissionRequest / PermissionDenied → trace spans.

Surface the tool + reason in the trace DB so dashboards can answer "which
tools get denied most often?" without us needing to keep a separate log.
No auto-approve, no auto-retry — the human (or the model, in auto mode)
makes the next move.

No `additional_context` — the denial propagates to the model via the
tool's error response; the request is user-facing UI (silent-trace policy,
commit `fa3922e`).
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse


def handle_pre_tool_request(payload: HookPayload) -> HookResponse | None:
    """Provider fallback for permission requests carried on PreToolUse."""
    info = payload.permission_request
    if info is None:
        return None
    try:
        _emit_span(payload, 'permission.request')
    except Exception:
        pass
    return payload.resolved_provider.serialize_permission_decision(info)


def handle_request(payload: HookPayload) -> HookResponse | None:
    try:
        _emit_span(payload, 'permission.request')
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def handle_denied(payload: HookPayload) -> HookResponse | None:
    try:
        _emit_span(payload, 'permission.denied', status='ERROR')
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def _emit_span(payload: HookPayload, name: str, status: str = 'OK') -> None:
    from lib.hook_plugin import post_span  # type: ignore
    attrs: dict = {}
    tool = payload.tool_name
    if tool:
        attrs['tool_name'] = tool
    reason = payload.raw.get('reason') or payload.raw.get('message')
    if reason:
        attrs['reason'] = str(reason)[:500]
    info = payload.permission_request
    if info is not None:
        attrs['requested_permission'] = info.requested_permission[:500]
        attrs['option_count'] = len(info.options)
        if info.default_option_id:
            attrs['default_option_id'] = info.default_option_id
    # When the user denies an AskUserQuestion, no PostToolUse/PostToolUseFailure
    # fires, so the `tool.AskUserQuestion` span (which carries the question
    # text) is never written. Capture the input here so the trace can still
    # show what the agent asked even when the answer never arrived.
    if tool == 'AskUserQuestion':
        questions = (payload.tool_input or {}).get('questions') or []
        if questions:
            from .post_tool_trace import _ask_option
            attrs['questions'] = [
                {
                    'question': q.get('question'),
                    'header': q.get('header'),
                    'options': [_ask_option(o) for o in (q.get('options') or [])],
                    'multiSelect': q.get('multiSelect', False),
                }
                for q in questions
            ]
    tu_id = (payload.raw or {}).get('tool_use_id')
    if isinstance(tu_id, str) and tu_id:
        attrs['tool_use_id'] = tu_id
    post_span(
        trace_id=payload.session_id,
        name=name,
        attributes=attrs,
        status_code=status,
    )
