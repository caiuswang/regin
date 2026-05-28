"""Handlers: TaskCreated / TaskCompleted → trace spans.

Lightweight trace markers. Neither gates nor blocks — TaskCreated returning
`decision:block` would roll back the task creation, which we never want.

No `additional_context` — the TaskCreate tool response already tells the
model what it just created (silent-trace policy, commit `fa3922e`).
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse

_SUBJECT_MAX = 60


def handle_created(payload: HookPayload) -> HookResponse | None:
    try:
        _emit_span(payload, 'task.created')
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def handle_completed(payload: HookPayload) -> HookResponse | None:
    try:
        _emit_span(payload, 'task.completed')
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def _emit_span(payload: HookPayload, name: str) -> None:
    from lib.hook_plugin import post_span  # type: ignore
    attrs: dict = {}
    raw = payload.raw
    task_id = raw.get('task_id') or (raw.get('task') or {}).get('id')
    subject = raw.get('task_subject') or (raw.get('task') or {}).get('subject')
    status = raw.get('status') or (raw.get('task') or {}).get('status')
    if task_id:
        attrs['task_id'] = task_id
    if subject:
        attrs['subject'] = subject if len(subject) <= _SUBJECT_MAX else subject[:_SUBJECT_MAX] + '…'
        attrs['subject_chars'] = len(subject)
    if status:
        attrs['status'] = status
    post_span(
        trace_id=payload.session_id,
        name=name,
        attributes=attrs,
    )
