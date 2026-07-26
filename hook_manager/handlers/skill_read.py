"""Handler: trace reads of `.claude/skills/<name>/content.md` files.

Preserves the existing ingest-side contract (`/api/skill-reads` POST + a
`skill.read` session span)."""

from __future__ import annotations

import os

from ..core import HookPayload, HookResponse


def _resolve_skill_read(payload: HookPayload) -> tuple[str, str | None]:
    """Return the read file path paired with its skill id (None if not a skill).

    Kimi's Read payload names the file under `path` and may make it
    repo-relative, so match the raw value first — a project-scoped
    `.agents/skills/<name>/content.md` read only resolves *before* it is
    joined to cwd — then retry absolutized for home-scoped skill trees.
    """
    tool_input = payload.tool_input or {}
    fp = tool_input.get('file_path') or tool_input.get('path') or ''
    from lib.hook_plugin import skill_id_from_read_path
    agent_type = getattr(payload.resolved_provider, 'provider_id', None)
    sid = skill_id_from_read_path(fp, agent_type=agent_type)
    if sid or not fp or os.path.isabs(fp) or not payload.cwd:
        return fp, sid
    absolute = os.path.normpath(os.path.join(payload.cwd, fp))
    return absolute, skill_id_from_read_path(absolute, agent_type=agent_type)


def handle(payload: HookPayload) -> HookResponse | None:
    if payload.tool_name != 'Read':
        return None
    fp, sid = _resolve_skill_read(payload)
    if not sid:
        # Silent — no point telling the model every Read that wasn't a skill.
        return None
    # Trace emission (best-effort); the legacy hook_plugin.post_event is the
    # canonical ingest path — we delay importing it so the handler stays
    # testable without the trace API running.
    try:
        _emit_span(payload, sid, fp)
    except Exception:
        pass
    return HookResponse(
        suppress_output=True,
        additional_context=f'skill-read-trace: logged read of {sid}',
    )


def _emit_span(payload: HookPayload, skill_id: str, file_path: str) -> None:
    from lib.hook_plugin import post_span, post_event  # type: ignore
    post_span(
        trace_id=payload.session_id,
        name='skill.read',
        attributes={'skill_id': skill_id, 'file_path': file_path, 'found': True},
    )
    post_event('skill_reads', {
        'skill_id': skill_id,
        'session_id': payload.session_id,
        'file_path': file_path,
        'found': True,
    }, agent_type=getattr(payload.resolved_provider, 'provider_id', None))
