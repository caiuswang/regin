"""skill_experience_block: the shared <skill_experience> builder behind both
the slash-command and Skill-tool delivery paths."""

from __future__ import annotations

import lib.memory as memory
from lib.memory.skill_experience import (leaf_id_for_skill,
                                         skill_experience_block)

_LEAF = "skill-playwright-screenshots"


def _seed():
    mid = memory.remember(
        "Playwright reuseExistingServer keeps a stale Python on :8321; "
        "restart the backend after edits or E2E asserts against old code.",
        kind="gotcha", title="Restart backend for E2E")
    memory.get_store().link_authoritative_topic(mid, _LEAF, source="manual")
    return mid


def test_leaf_id_strips_slash():
    assert leaf_id_for_skill("/playwright-screenshots") == _LEAF
    assert leaf_id_for_skill("playwright-screenshots") == _LEAF
    assert leaf_id_for_skill("") is None


def test_block_built_for_filed_skill():
    _seed()
    block = skill_experience_block("playwright-screenshots", "s1")
    assert "<skill_experience>" in block
    assert "Restart backend for E2E" in block
    assert "playwright-screenshots" in block


def test_block_empty_for_unknown_or_unfiled_skill():
    assert skill_experience_block("playwright-screenshots", "s1") == ""  # none filed
    _seed()
    assert skill_experience_block("not-a-skill", "s1") == ""  # no meta-leaf


def test_block_respects_flag(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "skill_experience_inject", False)
    _seed()
    assert skill_experience_block("playwright-screenshots", "s1") == ""


def test_block_records_injection_for_feedback():
    mid = _seed()
    skill_experience_block("playwright-screenshots", "s1")
    assert mid in memory.get_store().injected_memory_ids("s1")


# ── the PreToolUse Skill-tool handler (auto-invoked skills) ─────────────

def _skill_payload(skill, **extra):
    from hook_manager.core import HookPayload
    raw = {"hook_event_name": "PreToolUse", "tool_name": "Skill",
           "tool_input": ({"skill": skill} if skill is not None else {}),
           "session_id": "s1", **extra}
    return HookPayload.from_stdin_json("PreToolUse", raw)


def test_handler_injects_on_auto_invoke():
    from hook_manager.handlers import skill_experience
    _seed()
    r = skill_experience.handle(_skill_payload("playwright-screenshots"))
    assert r is not None and "<skill_experience>" in r.additional_context
    assert "Restart backend for E2E" in r.additional_context


def test_handler_none_for_non_skill_tool():
    from hook_manager.core import HookPayload
    from hook_manager.handlers import skill_experience
    _seed()
    p = HookPayload.from_stdin_json("PreToolUse", {
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": "ls"}})
    assert skill_experience.handle(p) is None


def test_handler_none_without_skill_arg_or_filed_memory():
    from hook_manager.handlers import skill_experience
    # no skill arg
    assert skill_experience.handle(_skill_payload(None)) is None
    # skill arg but nothing filed
    assert skill_experience.handle(_skill_payload("playwright-screenshots")) is None
