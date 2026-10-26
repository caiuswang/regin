"""`regin uninstall` removes only what this checkout can prove it wrote
(F156 R5, invariants 4 and 5)."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli import output as cli_output
from cli.app import app
from cli.commands import hooks as hooks_cmd
from lib import hooks_wiring
from lib import uninstall as uninstall_lib
from lib.providers.claude import ClaudeProvider
from lib.providers.kimi import KimiProvider
from lib.settings import settings
from lib.skills import skill_deployer

_OTHER = "/other/regin/.venv/bin/python -P -m hook_manager PreToolUse --agent-type claude"


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """A main checkout (not a worktree) with hooks for two providers, 2 recorded
    skills, 1 user skill, the launcher symlink, hook-manager configs, data dir
    and DB."""
    checkout = tmp_path / "checkout"
    (checkout / "db").mkdir(parents=True)
    (checkout / "db" / "regin.db").write_text("db")
    monkeypatch.setattr(settings, "project_root", checkout)
    monkeypatch.setattr(settings, "main_worktree", checkout)
    home = tmp_path / "home"
    claude = ClaudeProvider({
        "hook_settings_path": home / ".claude" / "settings.json",
        "hook_manager_config_path": home / ".claude" / "hook-manager-config.json",
    })
    kimi = KimiProvider({
        "hook_settings_path": home / ".kimi" / "config.toml",
        "hook_manager_config_path": home / ".kimi" / "hook-manager-config.json",
        "hook_payload_log_path": home / ".kimi" / "hook-payloads.jsonl",
    })
    providers = [claude, kimi]
    for p in providers:
        Path(p.hook_settings_path()).parent.mkdir(parents=True, exist_ok=True)
        assert hooks_wiring.install_hook_manager(p, str(p.hook_settings_path()))["ok"]
        Path(p.hook_manager_config_path()).write_text("{}")
    assert hooks_wiring.install_debug_hook(claude, str(claude.hook_settings_path()))["ok"]

    settings_json = Path(claude.hook_settings_path())

    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setattr(settings, "skills_dir", str(skills))
    for sid in ("alpha", "beta"):
        src = tmp_path / "src" / sid
        src.mkdir(parents=True)
        (src / "SKILL.md").write_text(f"---\ntitle: {sid}\n---\nbody")
        skill_deployer.deploy_pattern_as_skill(str(src), sid, sid.title())
    (skills / "mine").mkdir()
    (skills / "mine" / "SKILL.md").write_text("user-authored")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    os.symlink(checkout / ".venv" / "bin" / "regin", bin_dir / "regin")

    data_dir = tmp_path / "data"
    (data_dir / "patterns").mkdir(parents=True)
    monkeypatch.setattr(settings, "data_dir", data_dir)

    monkeypatch.setattr(uninstall_lib, "hook_providers", lambda: providers)
    monkeypatch.setattr(hooks_cmd, "_providers", lambda _pid=None: providers)
    return {"providers": providers, "skills": skills, "bin_dir": bin_dir,
            "data_dir": data_dir, "db": checkout / "db" / "regin.db",
            "home": home, "settings_json": settings_json, "checkout": checkout}


def _add_other_checkout_entry(settings_json: Path) -> None:
    data = json.loads(settings_json.read_text())
    data["hooks"].setdefault("PreToolUse", []).append(
        {"matcher": "*", "hooks": [{"type": "command", "command": _OTHER}]})
    settings_json.write_text(json.dumps(data))


def _snapshot(*roots: Path) -> dict[str, str]:
    out = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                p = os.path.join(dirpath, name)
                if os.path.islink(p):
                    out[p] = "->" + os.readlink(p)
                elif os.path.isfile(p):
                    out[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
                else:
                    out[p] = "dir"
    return out


@pytest.fixture
def run(monkeypatch):
    """Invoke the CLI; returns (exit code, everything it printed)."""
    def _run(*args: str, input: str | None = None):
        sink = io.StringIO()
        monkeypatch.setattr(cli_output, "_stdout", sink)
        monkeypatch.setattr(cli_output, "_stderr", sink)
        result = CliRunner().invoke(app, list(args), input=input)
        return result.exit_code, sink.getvalue() + result.output
    return _run


def _owned_listing(installed) -> list[str]:
    claude, kimi = installed["providers"]
    return ["claude hook_manager hooks", "claude debug hooks", "kimi hook_manager hooks",
            str(installed["skills"] / "alpha"), str(installed["skills"] / "beta"),
            str(installed["bin_dir"] / "regin"),
            str(claude.hook_manager_config_path()), str(kimi.hook_manager_config_path())]


def test_uninstall_dry_run_lists_only_owned_artifacts(installed, run, tmp_path):
    before = _snapshot(tmp_path)
    code, out = run("uninstall", "--dry-run", "--bin-dir", str(installed["bin_dir"]))
    assert code == 0, out
    removing, _, left = out.partition("Left in place")

    missing = [item for item in _owned_listing(installed) if item not in removing]
    assert missing == []
    assert "mine" not in out
    assert str(installed["data_dir"]) not in removing
    assert left == ""
    assert all(step in out for step in ("rm -rf", "/plugin uninstall", "statusLine"))
    assert _snapshot(tmp_path) == before


def test_uninstall_removes_owned_artifacts_and_keeps_data(installed, run):
    code, out = run("uninstall", "--yes", "--bin-dir", str(installed["bin_dir"]))
    assert code == 0, out

    code, out = run("hooks", "status", "--json")
    assert code == 0, out
    rows = json.loads(out[out.index("["):])
    assert len(rows) == 5  # claude: hook_manager, debug, mods; kimi: hook_manager, debug
    assert [r for r in rows if r["installed"]] == []

    assert not (installed["skills"] / "alpha").exists()
    assert not (installed["skills"] / "beta").exists()
    assert (installed["skills"] / "mine" / "SKILL.md").read_text() == "user-authored"
    assert not os.path.lexists(installed["bin_dir"] / "regin")
    assert [p for p in installed["providers"] if Path(p.hook_manager_config_path()).exists()] == []

    assert installed["db"].exists()
    assert installed["data_dir"].exists()

    from lib.patterns import pattern_deployments
    assert pattern_deployments.list_deployments() == []


def test_uninstall_purge_data_deletes_data_dir_and_db(installed, run):
    code, out = run("uninstall", "--yes", "--purge-data", "--bin-dir", str(installed["bin_dir"]))
    assert code == 0, out
    assert not installed["data_dir"].exists()
    assert not installed["db"].exists()


def test_uninstall_leaves_a_launcher_pointing_elsewhere(installed, run, tmp_path):
    link = installed["bin_dir"] / "regin"
    link.unlink()
    os.symlink(tmp_path / "other-checkout" / ".venv" / "bin" / "regin", link)
    code, out = run("uninstall", "--yes", "--bin-dir", str(installed["bin_dir"]))
    assert code == 0, out
    assert os.path.islink(link)
    assert "not this checkout's" in out


def test_uninstall_without_yes_asks_and_aborts_on_no(installed, run, tmp_path):
    before = _snapshot(tmp_path)
    code, out = run("uninstall", "--bin-dir", str(installed["bin_dir"]), input="n\n")
    assert code == 1
    assert "Proceed?" in out
    assert _snapshot(tmp_path) == before


def test_uninstall_keeps_shared_artifacts_while_another_checkout_is_wired(installed, run):
    _add_other_checkout_entry(installed["settings_json"])
    code, out = run("uninstall", "--yes", "--purge-data", "--bin-dir", str(installed["bin_dir"]))
    assert code == 0, out

    remaining = json.loads(installed["settings_json"].read_text())["hooks"]
    assert [h["command"] for e in remaining.pop("PreToolUse") for h in e["hooks"]] == [_OTHER]
    assert remaining == {}
    assert not os.path.lexists(installed["bin_dir"] / "regin")
    survivors = [installed["skills"] / "alpha", installed["skills"] / "beta",
                 installed["db"], installed["data_dir"],
                 *(Path(p.hook_manager_config_path()) for p in installed["providers"])]
    assert [p for p in survivors if not p.exists()] == []
    assert "shared with /other/regin" in out


def test_uninstall_from_a_worktree_keeps_shared_artifacts(installed, run, tmp_path, monkeypatch):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(settings, "project_root", worktree)
    code, out = run("uninstall", "--dry-run", "--purge-data", "--bin-dir", str(installed["bin_dir"]))
    assert code == 0, out
    removing, _, left = out.partition("Left in place")
    assert str(installed["skills"] / "alpha") in left
    assert str(installed["db"]) in left
    assert "this is a worktree" in left
    assert str(installed["skills"]) not in removing


def test_uninstall_reports_an_unreadable_settings_file_and_carries_on(installed, run):
    installed["settings_json"].write_text("[1, 2]")
    code, out = run("uninstall", "--yes", "--bin-dir", str(installed["bin_dir"]))
    assert code == 0, out
    assert "could not read" in out
    assert installed["settings_json"].read_text() == "[1, 2]"
    assert "rm -rf" in out
    assert (installed["skills"] / "alpha").exists()


@pytest.mark.parametrize("which, broken", [
    (0, '{"hooks": {"PreToolUse": [{"hooks": [{"command": "/other/regin/.venv/bin/python"'),
    (1, '[[hooks]]\nevent = "PreToolUse"\ncommand = "/other/regin/.venv/bin/python -m hook_manager'),
])
def test_uninstall_keeps_shared_artifacts_when_a_hook_config_will_not_parse(
        installed, run, which, broken):
    path = Path(installed["providers"][which].hook_settings_path())
    path.write_text(broken)
    code, out = run("uninstall", "--yes", "--bin-dir", str(installed["bin_dir"]))
    assert code == 0, out
    assert "could not read" in out
    assert path.read_text() == broken
    assert (installed["skills"] / "alpha").exists()
