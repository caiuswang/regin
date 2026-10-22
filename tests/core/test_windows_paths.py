"""Windows venv layout, hook commands and launcher ownership, exercised on any
OS by flipping the `WINDOWS` flag each module reads."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lib import hooks_wiring, uninstall, venv_paths
from lib.providers.claude import ClaudeProvider
from lib.settings import settings
from lib.uninstall import Plan


@pytest.fixture
def windows(monkeypatch):
    for mod in (venv_paths, hooks_wiring, uninstall):
        monkeypatch.setattr(mod, "WINDOWS", True)


def test_venv_layout_uses_scripts_and_exe(windows):
    assert venv_paths.venv_python("/r") == os.path.join("/r", ".venv", "Scripts", "python.exe")
    assert venv_paths.venv_script("/r", "regin").endswith("regin.exe")


def test_hook_command_runs_the_scripts_interpreter_in_utf8_mode(windows, tmp_path):
    provider = ClaudeProvider({"hook_settings_path": tmp_path / "s.json"})
    cmd = hooks_wiring.hook_manager_command("Stop", provider)
    root = str(settings.project_root)
    assert cmd.startswith(hooks_wiring.interpreter_prefix())
    assert "/.venv/Scripts/python.exe -X utf8 -P -m hook_manager Stop" in cmd
    assert hooks_wiring.checkout_root(cmd) == root


def test_posix_hook_command_is_unchanged(tmp_path):
    provider = ClaudeProvider({"hook_settings_path": tmp_path / "s.json"})
    cmd = hooks_wiring.hook_manager_command("Stop", provider)
    assert "/.venv/bin/python -P -m hook_manager Stop" in cmd
    assert "-X utf8" not in cmd


def _launcher_plan(tmp_path, monkeypatch, copied: bytes) -> Plan:
    checkout = tmp_path / "checkout"
    scripts = checkout / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "regin.exe").write_bytes(b"launcher for checkout")
    monkeypatch.setattr(settings, "project_root", checkout)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "regin.exe").write_bytes(copied)
    plan = Plan()
    uninstall._plan_symlink(plan, bin_dir)
    return plan


def test_uninstall_removes_this_checkouts_copied_launcher(windows, tmp_path, monkeypatch):
    plan = _launcher_plan(tmp_path, monkeypatch, b"launcher for checkout")
    assert [Path(r.path).name for r in plan.removals] == ["regin.exe"]
    plan.removals[0].apply()
    assert not (tmp_path / "bin" / "regin.exe").exists()


def test_uninstall_keeps_another_checkouts_launcher(windows, tmp_path, monkeypatch):
    plan = _launcher_plan(tmp_path, monkeypatch, b"launcher for elsewhere")
    assert plan.removals == []
    assert [Path(k.path).name for k in plan.kept] == ["regin.exe"]
