"""Handler: trace explicit skill invocations via UserPromptExpansion.

Captures slash-command expansion events so the dashboard can distinguish
"user invoked /skillname" from "Claude read the skill file for other
reasons".  Emits the same ingest endpoint as skill_read.py so both event
types land in the skill_reads table (differentiated by source='invoke').
"""

from __future__ import annotations

from lib.providers import get_active_provider

from ..core import HookPayload, HookResponse


def handle(payload: HookPayload) -> HookResponse | None:
    if payload.event != 'UserPromptExpansion':
        return None

    raw = payload.raw
    command_name = raw.get('command_name')
    if not command_name:
        return None

    command_source = raw.get('command_source') or 'unknown'
    command_args = raw.get('command_args') or ''
    # Synthetic file_path so the ingest endpoint's non-null constraint is
    # satisfied while making invocation rows visually distinct from reads.
    file_path = get_active_provider().skill_invoke_path(command_name)

    try:
        _emit_span(payload, command_name, file_path, command_source, command_args)
    except Exception:
        pass

    return HookResponse(
        suppress_output=True,
        additional_context=f'skill-invoke-trace: logged invocation of {command_name}',
    )


def _emit_span(
    payload: HookPayload,
    command_name: str,
    file_path: str,
    command_source: str,
    command_args: str,
) -> None:
    from lib.hook_plugin import post_span, post_event  # type: ignore
    post_span(
        trace_id=payload.session_id,
        name='skill.invoke',
        attributes={
            'skill_id': command_name,
            'file_path': file_path,
            'command_source': command_source,
            'command_args': command_args,
            'found': True,
        },
    )
    post_event('skill_reads', {
        'skill_id': command_name,
        'session_id': payload.session_id,
        'file_path': file_path,
        'found': True,
        'source': 'invoke',
        'command_args': command_args,
    })
