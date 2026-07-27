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


# ── Legacy (unmarked) entries ────────────────────────────────────────
#
# Entries written before the delimited managed block existed carry no markers.
# Left unreachable, a stale command keeps firing forever: this is how a Kimi
# config ended up printing `{"suppressOutput": true}` on every prompt from a
# debug-hook command installed without `--silent`.

_LEGACY_DEBUG = '''
[[hooks]]
event = "UserPromptSubmit"
command = "/x/.venv/bin/python /x/scripts/hook_payload_debug.py"
timeout = 10

[[hooks]]
event = "PreToolUse"
command = "/x/.venv/bin/python /x/scripts/hook_payload_debug.py"
timeout = 10
'''

_SILENT_DEBUG = "/x/.venv/bin/python /x/scripts/hook_payload_debug.py /k/log.jsonl --silent"


def test_install_replaces_unmarked_legacy_entries(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING + _LEGACY_DEBUG)

    kimi_hooks.install(
        str(cfg), ["UserPromptSubmit", "PreToolUse", "PostToolUse"],
        lambda _e: _SILENT_DEBUG, timeout=10, label="debug",
        is_ours=_is_debug,
    )

    parsed = tomllib.loads(cfg.read_text())
    debug_cmds = [h["command"] for h in parsed["hooks"] if _is_debug(h["command"])]
    # Exactly one entry per event, all carrying the refreshed command — the
    # legacy copies are gone rather than duplicated alongside the new block.
    assert len(debug_cmds) == 3
    assert set(debug_cmds) == {_SILENT_DEBUG}
    assert 'default_model = "kimi-code/kimi-for-coding"' in cfg.read_text()


def test_uninstall_removes_unmarked_legacy_entries(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING + _LEGACY_DEBUG)

    assert kimi_hooks.uninstall(str(cfg), label="debug", is_ours=_is_debug) is True
    assert kimi_hooks.routed_events(str(cfg), _is_debug) == set()
    assert 'default_model = "kimi-code/kimi-for-coding"' in cfg.read_text()


def test_strip_entries_leaves_foreign_hooks_alone(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING + _LEGACY_DEBUG
                   + '\n[[hooks]]\nevent = "Stop"\ncommand = "prettier --write"\n')

    kimi_hooks.uninstall(str(cfg), label="debug", is_ours=_is_debug)

    parsed = tomllib.loads(cfg.read_text())
    assert [h["command"] for h in parsed["hooks"]] == ["prettier --write"]


def test_strip_entries_survives_bracket_leading_value_lines(tmp_path: Path):
    """An array value continued across lines starts with `[`. Reading that as
    the next table header splits the entry and orphans its tail — the file
    stops parsing as TOML and the user loses providers/models/oauth."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING + '''
[[hooks]]
event = "PreToolUse"
command = "/x/.venv/bin/python /x/scripts/hook_payload_debug.py"
matchers = [
  "Bash",
  "Read",
]
timeout = 10

[thinking]
enabled = true
''')

    kimi_hooks.uninstall(str(cfg), label="debug", is_ours=_is_debug)

    parsed = tomllib.loads(cfg.read_text())  # must still be valid TOML
    assert parsed.get("hooks", []) == []
    assert parsed["thinking"] == {"enabled": True}


def test_strip_entries_survives_a_nested_array_row_without_a_trailing_comma(tmp_path: Path):
    """`  [1, 2]` — a last array row, no trailing comma — is shape-identical to
    a table header. Only bracket depth tells them apart."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING + '''
[[hooks]]
event = "PreToolUse"
command = "/x/.venv/bin/python /x/scripts/hook_payload_debug.py"
grid = [
  [1, 2]
]
timeout = 10

[thinking]
enabled = true
''')

    kimi_hooks.uninstall(str(cfg), label="debug", is_ours=_is_debug)

    parsed = tomllib.loads(cfg.read_text())  # must still be valid TOML
    assert parsed.get("hooks", []) == []
    assert parsed["thinking"] == {"enabled": True}


def test_strip_entries_ignores_brackets_inside_a_command_string(tmp_path: Path):
    """A `[` in a quoted value must not open a phantom array and swallow the
    tables that follow."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING + '''
[[hooks]]
event = "PreToolUse"
command = "/x/.venv/bin/python /x/scripts/other.py --tag [beta"
timeout = 10

[thinking]
enabled = true
''')

    assert kimi_hooks.uninstall(str(cfg), label="debug", is_ours=_is_debug) is False
    parsed = tomllib.loads(cfg.read_text())
    assert parsed["thinking"] == {"enabled": True}
    assert len(parsed["hooks"]) == 1


def test_strip_entries_keeps_user_comments_around_the_entry(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING + '''
[[hooks]]
event = "PreToolUse"
command = "/x/.venv/bin/python /x/scripts/hook_payload_debug.py"
timeout = 10

# IMPORTANT: do not enable thinking on the free tier
[thinking]
enabled = true
''')

    kimi_hooks.uninstall(str(cfg), label="debug", is_ours=_is_debug)

    text = cfg.read_text()
    assert "# IMPORTANT: do not enable thinking on the free tier" in text
    assert tomllib.loads(text)["thinking"] == {"enabled": True}


def test_strip_entries_matches_header_with_trailing_comment(tmp_path: Path):
    """A missed header leaves the stale entry firing while the API reports
    success — the exact failure the fix exists to end."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING + '''
[[hooks]]  # legacy debug hook
event = "PreToolUse"
command = "/x/.venv/bin/python /x/scripts/hook_payload_debug.py"
timeout = 10
''')

    kimi_hooks.uninstall(str(cfg), label="debug", is_ours=_is_debug)

    assert kimi_hooks.routed_events(str(cfg), _is_debug) == set()


def test_strip_entries_ignores_table_header_inside_multiline_string(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING + '''
[[hooks]]
event = "PreToolUse"
command = "/x/.venv/bin/python /x/scripts/hook_payload_debug.py"
note = """
[thinking] is a table header, but only outside a string
"""
timeout = 10

[thinking]
enabled = true
''')

    kimi_hooks.uninstall(str(cfg), label="debug", is_ours=_is_debug)

    parsed = tomllib.loads(cfg.read_text())
    assert parsed.get("hooks", []) == []
    assert parsed["thinking"] == {"enabled": True}


def test_installed_commands_reports_stale_command(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(_EXISTING + _LEGACY_DEBUG)
    assert kimi_hooks.installed_commands(str(cfg), _is_debug) == {
        "/x/.venv/bin/python /x/scripts/hook_payload_debug.py"}
