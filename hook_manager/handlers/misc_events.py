"""Small, logging-only handlers for the remaining log-safe events.

Each emits a trace span to the session DB so the trace dashboard has a
record, but returns `HookResponse(suppress_output=True)` with no
`additional_context` — the model doesn't need a transcript breadcrumb for
orchestration events it can't act on (silent-trace policy, commit `fa3922e`).

Wiring note: WorktreeCreate, Elicitation, and ElicitationResult are NOT in
this module. They are *provider* hooks where the spec requires the handler
to emit structured output (worktree path, form action/content) — a
default-no-op handler would break the default Claude Code flow for users
who didn't opt in. Leave them unwired.
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse

_TASK_SOURCE_KINDS = frozenset({'background_task', 'task', 'agent', 'subagent'})
_FAILURE_SEVERITIES = frozenset({'error', 'critical'})


def teammate_idle(payload: HookPayload) -> HookResponse | None:
    raw = payload.raw
    _safe_emit(payload, 'teammate.idle', {
        'teammate_name': raw.get('teammate_name') or 'unknown',
    })
    return HookResponse(suppress_output=True)


def instructions_loaded(payload: HookPayload) -> HookResponse | None:
    raw = payload.raw
    attrs: dict = {}
    for key in ('file_path', 'memory_type', 'load_reason', 'parent_file_path'):
        v = raw.get(key)
        if v:
            attrs[key] = v
    _safe_emit(payload, 'instructions.loaded', attrs)
    return HookResponse(suppress_output=True)


def config_change(payload: HookPayload) -> HookResponse | None:
    raw = payload.raw
    attrs: dict = {}
    src = raw.get('config_source') or raw.get('source')
    if src:
        attrs['source'] = src
    file_path = raw.get('file_path')
    if file_path:
        attrs['file_path'] = file_path
    _safe_emit(payload, 'config.change', attrs)
    return HookResponse(suppress_output=True)


def notification(payload: HookPayload) -> HookResponse | None:
    fields = _task_notification_fields(payload.raw)
    if fields:
        _safe_emit(
            payload,
            'task.notification',
            fields,
            span_id=f"task-{fields['task_id'][:13]}",
        )
    return HookResponse(suppress_output=True)


def _task_notification_fields(raw: dict) -> dict | None:
    """Map a background-task Notification payload onto the `task.notification`
    attribute shape, or None when the notification isn't about a task.

    Claude has no hook for this: it injects a `<task-notification>` block as a
    prompt and `prompt_trace` parses it, so its own Notification payloads
    (idle/permission prompts, no `source_id`) must fall through here and emit
    nothing. Kimi instead fires a structured Notification per background task.
    """
    task_id = _text(raw, 'source_id') or _text(raw, 'task_id')
    if not task_id or not _is_task_notification(raw):
        return None
    title = _text(raw, 'title')
    body = _text(raw, 'body') or _text(raw, 'message')
    return _drop_empty({
        'task_id': task_id,
        'status': _task_status(raw),
        'summary': body or title,
        'text': _join_lines(title, body),
        'notification_type': _text(raw, 'notification_type'),
        'source_kind': _text(raw, 'source_kind'),
    })


def _is_task_notification(raw: dict) -> bool:
    return (
        _text(raw, 'source_kind') in _TASK_SOURCE_KINDS
        or _text(raw, 'notification_type').startswith('task.')
    )


def _task_status(raw: dict) -> str:
    notification_type = _text(raw, 'notification_type')
    if _text(raw, 'severity') in _FAILURE_SEVERITIES:
        return 'failed'
    return notification_type.rsplit('.', 1)[-1] or 'completed'


def _text(raw: dict, key: str) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) else ''


def _join_lines(*parts: str) -> str:
    return '\n'.join(part for part in parts if part)


def _drop_empty(attrs: dict) -> dict:
    return {key: value for key, value in attrs.items() if value}


def worktree_remove(payload: HookPayload) -> HookResponse | None:
    raw = payload.raw
    attrs: dict = {}
    path = raw.get('worktree_path') or raw.get('path')
    if path:
        attrs['path'] = path
    _safe_emit(payload, 'worktree.remove', attrs)
    return HookResponse(suppress_output=True)


def _safe_emit(
    payload: HookPayload,
    name: str,
    attrs: dict,
    span_id: str | None = None,
) -> None:
    try:
        from lib.hook_plugin import post_span  # type: ignore
        post_span(
            trace_id=payload.session_id,
            name=name,
            attributes=attrs,
            span_id=span_id,
        )
    except Exception:
        pass
