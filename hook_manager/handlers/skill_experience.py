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
        from lib.memory.skill_experience import skill_experience_block
        block = skill_experience_block(skill_id, payload.session_id)
    except Exception:
        return None  # memory must never block a tool call
    if not block:
        return None
    return HookResponse(suppress_output=True, additional_context=block)
