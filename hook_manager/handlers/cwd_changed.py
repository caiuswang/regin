"""Handler: CwdChanged — log working-directory changes to the trace DB.

No decision control per spec — cleanup/logging only. Useful for trace
dashboards that show where the session walked during a task.

No `additional_context` — `pwd` is always visible to the model via Bash
(silent-trace policy, commit `fa3922e`).
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse


def handle(payload: HookPayload) -> HookResponse | None:
    try:
        _emit_span(payload)
    except Exception:
        pass
    return HookResponse(suppress_output=True)


def _emit_span(payload: HookPayload) -> None:
    from lib.hook_plugin import post_span  # type: ignore
    attrs: dict = {}
    cwd = payload.cwd or payload.raw.get('cwd') or payload.raw.get('new_cwd')
    if cwd:
        attrs['cwd'] = cwd
    old = payload.raw.get('old_cwd') or payload.raw.get('previous_cwd')
    if old:
        attrs['old_cwd'] = old
    post_span(
        trace_id=payload.session_id,
        name='cwd.changed',
        attributes=attrs,
    )
