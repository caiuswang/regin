"""Feature #168 S1 — the codebuddy and workbuddy provider skeleton."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib import settings as settings_mod
from lib.providers import build_provider, canonical_agent_kind, list_provider_ids
from lib.providers.codebuddy import CodeBuddyFamilyProvider
from lib.tasks.arm import parse_arm
from lib.tasks.capture import CaptureRejected

FAMILY = {"codebuddy": ".codebuddy", "workbuddy": ".workbuddy"}


@pytest.fixture(autouse=True)
def _isolate_provider_settings(monkeypatch):
    monkeypatch.setattr(settings_mod.settings, "providers", {})
    monkeypatch.setattr(settings_mod.settings, "active_provider", "claude")
    monkeypatch.setattr(settings_mod.settings, "codebuddy_config_dir", None)
    monkeypatch.setattr(settings_mod.settings, "workbuddy_config_dir", None)


def test_providers_registered(flask_client, monkeypatch):
    assert {"codebuddy", "workbuddy"} <= set(list_provider_ids())
    monkeypatch.setattr(settings_mod.settings, "experimental_providers", True)

    body = flask_client.get("/api/providers").get_json()

    rows = {row["id"]: row for row in body["providers"]}
    all_on = {"skills": True, "hooks": True, "sessions": True,
              "transcript_usage": True}
    assert rows["codebuddy"]["capabilities"] == all_on
    assert rows["workbuddy"]["capabilities"] == all_on
    assert rows["codebuddy"]["name"] == "CodeBuddy Code"
    assert rows["workbuddy"]["name"] == "WorkBuddy"


def test_workbuddy_cannot_be_launched(flask_client):
    body = flask_client.get("/api/agent-runs/launch-options").get_json()

    ids = {row["id"] for row in body["providers"]}
    assert "workbuddy" not in ids
    assert build_provider("workbuddy").launch_capabilities()["launch_modes"] == []
    with pytest.raises(CaptureRejected):
        parse_arm({"provider": "workbuddy"})


def test_codebuddy_launch_stays_closed_in_this_slice():
    assert build_provider("codebuddy").launch_capabilities()["launch_modes"] == []


@pytest.mark.parametrize("pid,root", FAMILY.items())
def test_default_paths_live_under_config_root(pid, root):
    p = build_provider(pid)
    home = Path.home() / root
    assert isinstance(p, CodeBuddyFamilyProvider)
    assert p.config_root() == home
    assert p.hook_settings_path() == home / "settings.json"
    assert p.global_skills_dir() == home / "skills"
    assert p.transcript_projects_dir() == home / "projects"
    assert p.hook_config_format == "json"
    assert p.hook_output_format == "claude"
    assert p.transcript_format == "codebuddy"


def test_mcp_config_file_differs_by_product():
    assert build_provider("codebuddy").mcp_config_path() == (
        Path.home() / ".codebuddy" / ".mcp.json")
    assert build_provider("workbuddy").mcp_config_path() == (
        Path.home() / ".workbuddy" / "mcp.json")
    assert build_provider("codebuddy").family.mcp_needs_user_trust is False
    assert build_provider("workbuddy").family.mcp_needs_user_trust is True


def test_codebuddy_project_skills_subpath():
    assert build_provider("codebuddy").project_skills_subpath() == (
        ".codebuddy", "skills")


def test_config_dir_env_override_goes_through_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEBUDDY_CONFIG_DIR", str(tmp_path / "cb"))
    monkeypatch.setenv("WORKBUDDY_CONFIG_DIR", str(tmp_path / "wb"))
    fresh = settings_mod.Settings()
    assert fresh.codebuddy_config_dir == tmp_path / "cb"
    assert fresh.workbuddy_config_dir == tmp_path / "wb"

    monkeypatch.setattr(settings_mod.settings, "codebuddy_config_dir",
                        fresh.codebuddy_config_dir)
    monkeypatch.setattr(settings_mod.settings, "workbuddy_config_dir",
                        fresh.workbuddy_config_dir)
    assert build_provider("codebuddy").hook_settings_path() == (
        tmp_path / "cb" / "settings.json")
    assert build_provider("workbuddy").mcp_config_path() == (
        tmp_path / "wb" / "mcp.json")


@pytest.mark.parametrize("pid", sorted(FAMILY))
def test_hook_events_match_claude(pid):
    assert build_provider(pid).hook_events() == build_provider("claude").hook_events()


@pytest.mark.parametrize("pid,root", FAMILY.items())
def test_find_session_transcript_by_session_id(pid, root, monkeypatch, tmp_path):
    setting = f"{pid}_config_dir"
    monkeypatch.setattr(settings_mod.settings, setting, tmp_path / root)
    transcript = tmp_path / root / "projects" / "some-slug" / "abc-123.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n")
    p = build_provider(pid)
    assert p.find_session_transcript("abc-123") == str(transcript)
    assert p.find_session_transcript("missing") is None


@pytest.mark.parametrize("pid,root", FAMILY.items())
def test_skill_content_path_detection(pid, root):
    p = build_provider(pid)
    assert p.skill_content_relpath("demo") == f"{root}/skills/demo/content.md"
    assert p.skill_id_from_read_path(
        f"/home/u/{root}/skills/demo/content.md", home="/home/u") == "demo"
    assert p.skill_id_from_read_path(
        "/home/u/.claude/skills/demo/content.md", home="/home/u") is None


@pytest.mark.parametrize("pid", sorted(FAMILY))
def test_agent_kind_maps_to_provider_id(pid):
    assert canonical_agent_kind(pid) == pid


def test_existing_hook_events_unchanged():
    assert build_provider("claude").hook_events() is None
    assert build_provider("codex").hook_events() == (
        "SessionStart", "SessionEnd", "UserPromptSubmit",
        "PreToolUse", "PostToolUse", "Stop")
    assert "PreToolUse" in build_provider("kimi").hook_events()


@pytest.mark.parametrize("pid,subpath", [
    ("claude", (".claude", "skills")),
    ("kimi", (".kimi-code", "skills")),
    ("codex", (".codex", "skills")),
])
def test_existing_skill_dirs_unchanged(pid, subpath):
    p = build_provider(pid)
    assert p.project_skills_subpath() == subpath
    assert str(p.global_skills_dir()).endswith("/".join(subpath))


@pytest.mark.parametrize("pid", ["claude", "kimi", "codex", "generic"])
def test_new_seams_default_to_none(pid):
    p = build_provider(pid)
    assert p.mcp_config_path() is None
    assert p.acp_argv() is None
