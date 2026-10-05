"""Handlers: settle background shells from what the harness states outright.

`Stop` carries the session's in-flight background tasks, and a successful
`TaskStop` names the task it killed. Both are identifier-keyed facts, so
neither needs the output-file marker to guess that a shell ended.
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse


def handle_stop(payload: HookPayload) -> HookResponse | None:
    """Settle the running shells the `Stop` snapshot no longer lists.

    `background_tasks` absent means this harness does not report one (Kimi,
    Codex, older Claude Code) — not that nothing is in flight — so it settles
    nothing.
    """
    snapshot = (payload.raw or {}).get('background_tasks')
    if not payload.session_id or not isinstance(snapshot, list):
        return None
    in_flight = {t['id'] for t in snapshot
                 if isinstance(t, dict) and isinstance(t.get('id'), str)}
    try:
        from lib.trace.repair import settle_shells_out_of_flight
        settle_shells_out_of_flight(payload.session_id, in_flight)
    except Exception:
        pass
    return None


def handle_task_stop(payload: HookPayload) -> HookResponse | None:
    response = payload.tool_response if isinstance(payload.tool_response, dict) else {}
    if response.get('task_type') != 'local_bash':
        return None
    task_id = response.get('task_id') or (payload.tool_input or {}).get('task_id')
    if not payload.session_id or not isinstance(task_id, str) or not task_id:
        return None
    try:
        from lib.trace.repair import record_shell_kill
        record_shell_kill(payload.session_id, task_id)
    except Exception:
        pass
    return None
