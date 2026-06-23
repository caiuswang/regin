"""Handler: inject `<skill_experience>` when a skill is auto-invoked.

Fires on PreToolUse for the `Skill` tool — the case where the assistant
itself launches a skill (e.g. topic-router) rather than the user typing a
slash command (handled in `memory_recall.py`). PreToolUse `additionalContext`
lands before the skill body, so the past-session lessons filed under the
skill's `skill-<id>` meta-leaf are visible as the skill's guidance loads.

Shares its block builder with the slash-command path via
`lib.memory.skill_experience`, so the two delivery routes can never drift.
"""

from __future__ import annotations

from ..core import HookPayload, HookResponse


def handle(payload: HookPayload) -> HookResponse | None:
    if payload.tool_name != "Skill" or payload.is_workflow_subagent:
        return None
    skill_id = (payload.tool_input or {}).get("skill")
    if not skill_id:
        return None
    try:
        from lib.memory.skill_experience import (
            emit_skill_experience_span, skill_experience_injection)
        block, mems = skill_experience_injection(skill_id, payload.session_id)
    except Exception:
        return None  # memory must never block a tool call
    if not block:
        return None
    # Record the injection on the trace so it shows in the session detail.
    # PreToolUse fires mid-turn, so this span chronologically grafts under the
    # active prompt (it is NOT a submit-time span). Best-effort inside.
    raw = payload.raw or {}
    emit_skill_experience_span(
        payload.session_id, skill_id, block, mems,
        agent_id=raw.get('agent_id'), agent_type=raw.get('agent_type'))
    return HookResponse(suppress_output=True, additional_context=block)
