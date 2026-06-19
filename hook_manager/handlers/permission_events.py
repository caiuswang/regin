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
        _emit_span(payload, 'permission.request', status='PENDING')
    except Exception:
        pass
    return payload.resolved_provider.serialize_permission_decision(info)


def handle_request(payload: HookPayload) -> HookResponse | None:
    try:
        _emit_span(payload, 'permission.request', status='PENDING')
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def handle_denied(payload: HookPayload) -> HookResponse | None:
    try:
        _emit_span(payload, 'permission.denied', status='ERROR')
        # The prompt is resolved — clear any pending push card for it.
        _maybe_resolve_push(payload)
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def _maybe_notify_push(payload: HookPayload, attrs: dict) -> None:
    """Opt-in: surface a pending permission prompt to the inbox + push
    channels — but only when it actually awaits a human. A request the
    harness auto-resolves (e.g. `bypassPermissions`, or an edit under
    `acceptEdits`) is not a blocker, so skip it rather than seed noise.
    Isolated so a notify failure can't disturb the trace span."""
    try:
        if not payload.resolved_provider.permission_awaits_human(payload):
            return
        from lib.agent_messages import event_notify  # type: ignore
        event_notify.notify_permission_request(
            trace_id=payload.session_id, attrs=attrs)
    except Exception:
        pass


def _maybe_resolve_push(payload: HookPayload) -> None:
    try:
        from lib.agent_messages import event_notify  # type: ignore
        event_notify.resolve_permission(payload.session_id)
    except Exception:
        pass


def _emit_span(payload: HookPayload, name: str, status: str = 'OK') -> None:
    from lib.hook_plugin import post_span  # type: ignore
    attrs = _build_perm_attrs(payload)
    if name == 'permission.request':
        _maybe_notify_push(payload, attrs)
    tu_id = attrs.get('tool_use_id')
    # A pending `permission.request` gets a deterministic id keyed on the
    # gated call's tool_use_id so `ingest_session_spans` can retire it when
    # the request resolves (the granting PostToolUse, or `permission.denied`).
    # Without a tool_use_id it falls back to a random id (no retirement).
    span_id = None
    if name == 'permission.request' and isinstance(tu_id, str) and tu_id:
        from lib.trace.pending_spans import perm_pending_id  # type: ignore
        span_id = perm_pending_id(tu_id)
    post_span(
        trace_id=payload.session_id,
        name=name,
        attributes=attrs,
        status_code=status,
        span_id=span_id,
    )


def _build_perm_attrs(payload: HookPayload) -> dict:
    """Attributes shared by permission.request / permission.denied spans."""
    attrs: dict = {}
    tool = payload.tool_name
    if tool:
        attrs['tool_name'] = tool
    reason = payload.raw.get('reason') or payload.raw.get('message')
    if reason:
        attrs['reason'] = str(reason)[:500]
    _apply_permission_info(attrs, payload.permission_request)
    # When the user denies an AskUserQuestion, no PostToolUse/PostToolUseFailure
    # fires, so the `tool.AskUserQuestion` span (which carries the question
    # text) is never written. Capture the input here so the trace can still
    # show what the agent asked even when the answer never arrived.
    if tool == 'AskUserQuestion':
        questions = _ask_questions_attr(payload.tool_input or {})
        if questions:
            attrs['questions'] = questions
    tu_id = (payload.raw or {}).get('tool_use_id')
    if isinstance(tu_id, str) and tu_id:
        attrs['tool_use_id'] = tu_id
    return attrs


def _apply_permission_info(attrs: dict, info) -> None:
    """Fold the provider-neutral PermissionRequestInfo fields into attrs."""
    if info is None:
        return
    attrs['requested_permission'] = info.requested_permission[:500]
    attrs['option_count'] = len(info.options)
    if info.default_option_id:
        attrs['default_option_id'] = info.default_option_id


def _ask_questions_attr(tool_input: dict) -> list[dict]:
    """Question structure for an AskUserQuestion permission span (mirrors
    `post_tool_trace._build_ask_attrs`, sans answers)."""
    from .post_tool_trace import _ask_option

    out: list[dict] = []
    for q in (tool_input.get('questions') or []):
        out.append({
            'question': q.get('question'),
            'header': q.get('header'),
            'options': [_ask_option(o) for o in (q.get('options') or [])],
            'multiSelect': q.get('multiSelect', False),
        })
    return out
