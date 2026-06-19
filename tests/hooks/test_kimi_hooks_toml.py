"""Tests for the Kimi config.toml [[hooks]] install/uninstall backend."""

from __future__ import annotations

import tomllib
from pathlib import Path

from lib.providers import kimi_hooks


_EXISTING = '''default_model = "kimi-code/kimi-for-coding"

[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://api.kimi.com/coding/v1"
'''


def _cmd_for(event: str) -> str:
    return f"/x/.venv/bin/python -P -m hook_manager {event} --agent-type kimi"


def _is_ours(command: str) -> bool:
    return "/x/.venv/bin/python " in command and "-m hook_manager" in command


def test_install_preserves_existing_config_and_routes_events(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING)

    kimi_hooks.install(str(cfg), ["PreToolUse", "PostToolUse", "Stop"], _cmd_for, timeout=60)

    text = cfg.read_text()
    # User-authored config is untouched.
    assert 'default_model = "kimi-code/kimi-for-coding"' in text
    assert '[providers."managed:kimi-code"]' in text
    # File is still valid TOML with three hook entries carrying our command.
    parsed = tomllib.loads(text)
    assert len(parsed["hooks"]) == 3
    assert all("--agent-type kimi" in h["command"] for h in parsed["hooks"])
    assert kimi_hooks.routed_events(str(cfg), _is_ours) == {"PreToolUse", "PostToolUse", "Stop"}


def test_install_is_idempotent(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING)
    events = ["PreToolUse", "PostToolUse"]
    kimi_hooks.install(str(cfg), events, _cmd_for)
    first = cfg.read_text()
    kimi_hooks.install(str(cfg), events, _cmd_for)
    assert cfg.read_text() == first
    assert kimi_hooks.routed_events(str(cfg), _is_ours) == set(events)


def test_uninstall_removes_only_managed_block(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING)
    kimi_hooks.install(str(cfg), ["PreToolUse"], _cmd_for)

    assert kimi_hooks.uninstall(str(cfg)) is True
    text = cfg.read_text()
    assert 'default_model = "kimi-code/kimi-for-coding"' in text
    assert "hook_manager" not in text
    assert kimi_hooks.routed_events(str(cfg), _is_ours) == set()
    # Second uninstall is a no-op.
    assert kimi_hooks.uninstall(str(cfg)) is False


def test_routed_events_ignores_foreign_hooks(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING + '\n[[hooks]]\nevent = "PostToolUse"\ncommand = "prettier --write"\n')
    # A user's own prettier hook is not ours and must not be reported as routed.
    assert kimi_hooks.routed_events(str(cfg), _is_ours) == set()


def test_routed_events_missing_file(tmp_path: Path):
    assert kimi_hooks.routed_events(str(tmp_path / "nope.toml"), _is_ours) == set()


def _is_debug(command: str) -> bool:
    return "hook_payload_debug" in command


def test_labelled_blocks_coexist_without_clobber(tmp_path: Path):
    """The hook_manager and debug fan-out hooks own separate labelled
    blocks in one config.toml; installing one must not wipe the other."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING)
    kimi_hooks.install(str(cfg), ["PreToolUse", "Stop"], _cmd_for)
    kimi_hooks.install(
        str(cfg), ["UserPromptSubmit", "PreToolUse"],
        lambda _e: "/x/.venv/bin/python /x/scripts/hook_payload_debug.py",
        timeout=10, label="debug",
    )

    text = cfg.read_text()
    assert tomllib.loads(text)  # still valid TOML
    # Both installs survive: hook_manager events and debug events both routed.
    assert kimi_hooks.routed_events(str(cfg), _is_ours) == {"PreToolUse", "Stop"}
    assert kimi_hooks.routed_events(str(cfg), _is_debug) == {
        "UserPromptSubmit", "PreToolUse"}
    # User config untouched.
    assert 'default_model = "kimi-code/kimi-for-coding"' in text

    # Uninstalling debug leaves hook_manager intact.
    assert kimi_hooks.uninstall(str(cfg), label="debug") is True
    assert kimi_hooks.routed_events(str(cfg), _is_debug) == set()
    assert kimi_hooks.routed_events(str(cfg), _is_ours) == {"PreToolUse", "Stop"}
