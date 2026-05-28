"""Handler: trace assistant-initiated `Skill` tool launches.

Captures the case where Claude itself invokes the Skill tool — neither a
content.md read (handled by skill_read) nor a slash-command (handled by
skill_invoke). Emits to the same `/api/skill-reads` endpoint with
`source='launch'` so the dashboard can show three disjoint signals.
"""

from __future__ import annotations

from lib.providers import get_active_provider

from ..core import HookPayload, HookResponse


def handle(payload: HookPayload) -> HookResponse | None:
    if payload.tool_name != 'Skill':
        return None
    skill_id = (payload.tool_input or {}).get('skill')
    if not skill_id:
        return None
    file_path = get_active_provider().skill_launch_path(skill_id)

    try:
        _emit_span(payload, skill_id, file_path)
    except Exception:
        pass

    return HookResponse(
        suppress_output=True,
        additional_context=f'skill-launch-trace: logged launch of {skill_id}',
    )


def _emit_span(payload: HookPayload, skill_id: str, file_path: str) -> None:
    from lib.hook_plugin import post_span, post_event  # type: ignore
    post_span(
        trace_id=payload.session_id,
        name='skill.launch',
        attributes={'skill_id': skill_id, 'file_path': file_path, 'found': True},
    )
    post_event('skill_reads', {
        'skill_id': skill_id,
        'session_id': payload.session_id,
        'file_path': file_path,
        'found': True,
        'source': 'launch',
    })
