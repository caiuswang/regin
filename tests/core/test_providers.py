"""Tests for provider registry and adapters."""

from __future__ import annotations

from lib.providers import build_provider, get_active_provider, list_provider_ids


def test_registry_lists_expected_ids():
    ids = list_provider_ids()
    assert ids == ["claude", "codex", "generic"]


def test_active_provider_defaults_to_claude():
    p = get_active_provider()
    assert p.provider_id == "claude"
    assert p.capabilities.skills is True
    assert p.permission_request_events() == ("PermissionRequest",)


def test_build_provider_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        build_provider("missing")


def test_claude_skill_content_path_detection():
    p = build_provider("claude")
    assert p.skill_id_from_read_path("/home/u/.claude/skills/demo/content.md", home="/home/u") == "demo"
    assert p.skill_id_from_read_path("/home/u/.claude/plans/x.md", home="/home/u") is None


def test_codex_provider_capabilities_are_enabled():
    p = build_provider("codex")
    assert p.capabilities.skills is True
    assert p.capabilities.hooks is True
    assert p.capabilities.sessions is True
    assert p.capabilities.transcript_usage is True
    assert p.hook_events() == (
        "SessionStart",
        "SessionEnd",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
    )
    assert p.permission_request_events() == ("PreToolUse",)


def test_generic_provider_capabilities_remain_stubbed():
    assert build_provider("generic").capabilities.hooks is False


def test_codex_skill_content_path_detection():
    p = build_provider("codex")
    assert p.skill_id_from_read_path("/home/u/.codex/skills/demo/content.md", home="/home/u") == "demo"
    assert p.skill_id_from_read_path("/home/u/.codex/plans/x.md", home="/home/u") is None
    assert str(p.hook_settings_path()).endswith("/.codex/hooks.json")
