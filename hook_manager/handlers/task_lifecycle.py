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


def _task_field(raw: dict, flat_key: str, nested_key: str):
    return raw.get(flat_key) or (raw.get('task') or {}).get(nested_key)


def _emit_span(payload: HookPayload, name: str) -> None:
    from lib.hook_plugin import post_span, stamp_source_prompt_id  # type: ignore
    attrs: dict = {}
    raw = payload.raw
    task_id = _task_field(raw, 'task_id', 'id')
    subject = _task_field(raw, 'task_subject', 'subject')
    status = _task_field(raw, 'status', 'status')
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
        attributes=stamp_source_prompt_id(attrs, payload.raw),
    )
