"""Tests for provider registry and adapters."""

from __future__ import annotations

import pytest

from lib.providers import build_provider, get_active_provider, list_provider_ids


@pytest.fixture(autouse=True)
def _isolate_provider_settings(monkeypatch):
    """Assert provider adapters against their built-in defaults.

    `settings.providers` and `active_provider` are persisted to the
    gitignored local settings file, so a developer who configured a path
    override through the Settings UI (e.g. a custom kimi `skills_dir`) would
    otherwise poison these default-path assertions. Reset them per test so
    the suite is hermetic regardless of local config.
    """
    from lib import settings as _s
    monkeypatch.setattr(_s.settings, "providers", {})
    monkeypatch.setattr(_s.settings, "active_provider", "claude")


def test_registry_lists_expected_ids():
    ids = list_provider_ids()
    assert ids == ["claude", "codex", "generic", "kimi"]


def _assert_kimi_skill_paths(p):
    # Skills deploy into ~/.kimi-code/skills, mirroring claude/codex shapes.
    assert str(p.global_skills_dir()).endswith(".kimi-code/skills")
    assert p.project_skills_subpath() == (".kimi-code", "skills")
    assert p.skill_invoke_path("demo") == ".kimi-code/skills/demo/invoke"
    assert p.skill_launch_path("demo") == ".kimi-code/skills/demo/launch"
    assert p.skill_content_relpath("demo") == ".kimi-code/skills/demo/content.md"
    assert p.skill_id_from_read_path(
        "/home/u/.kimi-code/skills/demo/content.md", home="/home/u") == "demo"
    # Kimi also reads from the cross-agent .agents/skills directory.
    assert p.skill_id_from_read_path(
        "/home/u/.agents/skills/demo/content.md", home="/home/u") == "demo"
    assert p.skill_id_from_read_path(
        "/home/u/.kimi-code/plans/x.md", home="/home/u") is None


def test_kimi_provider_paths_and_capabilities():
    p = build_provider("kimi")
    assert p.provider_id == "kimi"
    assert p.display_name == "Kimi Code"
    assert p.capabilities.hooks is True
    assert p.capabilities.skills is True
    # Kimi stores hooks in a TOML config, not settings.json.
    assert p.hook_config_format == "toml"
    assert str(p.hook_settings_path()).endswith(".kimi-code/config.toml")
    assert str(p.transcript_projects_dir()).endswith(".kimi-code/sessions")
    _assert_kimi_skill_paths(p)
    # All installed events are real spec events the router accepts.
    from hook_manager.core import SPEC_EVENTS
    events = p.hook_events()
    assert "PreToolUse" in events and "PostToolUse" in events
    assert set(events) <= set(SPEC_EVENTS)


def test_kimi_resolved_from_agent_type_and_model():
    from lib.providers import resolve_provider
    assert resolve_provider({"agent_type": "kimi"}).provider_id == "kimi"
    assert resolve_provider({"model": "kimi-code/kimi-for-coding"}).provider_id == "kimi"
    # Claude detection still wins for claude-* models.
    assert resolve_provider({"model": "claude-opus-4-8"}).provider_id == "claude"


def test_kimi_field_aliases_in_payload():
    from hook_manager.core import HookPayload
    p = HookPayload.from_stdin_json("PostToolUse", {
        "hook_event_name": "PostToolUse",
        "agent_type": "kimi",
        "session_id": "s1",
        "tool_name": "Shell",
        "tool_input": {"command": "ls"},
        "tool_output": {"stdout": "a\nb"},
        "tool_call_id": "tc_1",
    })
    assert p.resolved_provider.provider_id == "kimi"
    # Kimi's tool_output/tool_call_id are aliased to regin's canonical names.
    assert p.tool_response == {"stdout": "a\nb"}
    assert p.raw.get("tool_use_id") == "tc_1"


def test_kimi_string_tool_output_is_wrapped():
    from hook_manager.core import HookPayload
    p = HookPayload.from_stdin_json("PostToolUse", {
        "hook_event_name": "PostToolUse",
        "agent_type": "kimi",
        "session_id": "s1",
        "tool_name": "Shell",
        "tool_output": "bare string output",
    })
    assert p.tool_response == {"output": "bare string output"}


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


def test_provider_id_from_model_covers_vendors():
    from lib.providers import provider_id_from_model
    assert provider_id_from_model("claude-opus-4-8") == "claude"
    assert provider_id_from_model("kimi-code/kimi-for-coding") == "kimi"
    assert provider_id_from_model("gpt-4o") == "codex"
    assert provider_id_from_model("o3-mini") == "codex"
    assert provider_id_from_model("mystery-model") is None
    assert provider_id_from_model(None) is None


def test_canonical_agent_kind_mapping():
    from lib.providers import canonical_agent_kind
    assert canonical_agent_kind("claude") == "claude"
    assert canonical_agent_kind("openai") == "codex"
    assert canonical_agent_kind("codex") == "codex"
    assert canonical_agent_kind("kimi") == "kimi"
    # Any non-empty unknown vendor reads as generic; empty/None reads as None.
    assert canonical_agent_kind("workflow-subagent") == "generic"
    assert canonical_agent_kind("") is None
    assert canonical_agent_kind(None) is None


def test_only_codex_synthesizes_session_end_from_stop():
    # Capability gates the Stop->session.end fallback; only Codex opts in so a
    # per-turn Stop never prematurely ends a Claude/Kimi session.
    assert build_provider("codex").synthesizes_session_end_from_stop is True
    for pid in ("claude", "kimi", "generic"):
        assert build_provider(pid).synthesizes_session_end_from_stop is False


def test_tool_failure_error_text_normalizes_per_provider():
    # Claude/Codex carry a bare string; Kimi a {code,message,retryable} object.
    claude = build_provider("claude")
    assert claude.tool_failure_error_text("boom\n") == "boom"
    assert claude.tool_failure_error_text({"message": "x"}) == ""  # not Claude's shape

    kimi = build_provider("kimi")
    assert kimi.tool_failure_error_text(
        {"code": "internal", "message": "ls: no such file", "retryable": False}
    ) == "ls: no such file"
    # Falls back to the code when no message, and still handles a bare string.
    assert kimi.tool_failure_error_text({"code": "timeout"}) == "timeout"
    assert kimi.tool_failure_error_text("plain") == "plain"
    assert kimi.tool_failure_error_text(None) == ""


def test_kimi_detects_user_rejection_failures():
    kimi = build_provider("kimi")
    reject = {"code": "internal", "retryable": False,
              "message": 'Tool "Bash" was not run because the user rejected the approval request.'}
    assert kimi.tool_failure_is_user_rejection(reject) is True
    # A genuine tool error is NOT a rejection — it still gets a failure span.
    real = {"code": "internal", "message": "ls: /x: No such file or directory"}
    assert kimi.tool_failure_is_user_rejection(real) is False
    assert kimi.tool_failure_is_user_rejection(None) is False
    # Other providers never classify a failure as a rejection (different path).
    assert build_provider("claude").tool_failure_is_user_rejection(reject) is False


def test_kimi_normalizes_tool_response_envelope():
    kimi = build_provider("kimi")
    # Bash: Kimi's `{output}` becomes Claude's `stdout` so the Bash card renders.
    bash = kimi.normalize_tool_response("Bash", {"command": "ls"}, {"output": "a\nb"})
    assert bash["stdout"] == "a\nb"
    # Read: body → file.content (footer stripped) + total_lines lifted out.
    read = kimi.normalize_tool_response("Read", {"path": "x"}, {"output": (
        "1\thi\n<system>1 line read. Total lines in file: 1. "
        "End of file reached.</system>"
    )})
    assert read["file"]["content"] == "1\thi"
    assert read["file"]["total_lines"] == 1
    # A tool whose card derives from tool_input (Edit) passes through untouched.
    assert kimi.normalize_tool_response("Edit", {}, {"output": "ok"}) == {"output": "ok"}
    # An empty / non-string envelope is a no-op (no spurious stdout key).
    assert kimi.normalize_tool_response("Bash", {}, {"output": ""}) == {"output": ""}
    assert kimi.normalize_tool_response("Bash", {}, {}) == {}
    # An existing canonical key wins (setdefault never clobbers).
    keep = kimi.normalize_tool_response("Bash", {}, {"output": "x", "stdout": "y"})
    assert keep["stdout"] == "y"


def test_base_normalize_tool_response_is_passthrough():
    # Claude/Codex already send Claude-shaped keys; the base is identity.
    for pid in ("claude", "codex", "generic"):
        p = build_provider(pid)
        tr = {"stdout": "out", "stderr": ""}
        assert p.normalize_tool_response("Bash", {"command": "ls"}, tr) is tr


def test_codex_skill_content_path_detection():
    p = build_provider("codex")
    assert p.skill_id_from_read_path("/home/u/.codex/skills/demo/content.md", home="/home/u") == "demo"
    assert p.skill_id_from_read_path("/home/u/.codex/plans/x.md", home="/home/u") is None
    assert str(p.hook_settings_path()).endswith("/.codex/hooks.json")
