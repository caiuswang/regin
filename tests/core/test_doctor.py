"""Unit tests for lib.doctor.

Focuses on the pure / easily-mockable parts: _scan_hook_scripts (reads
a JSON file), _which (shutil.which wrapper), and the overall shape of
run_checks. Subprocess-heavy parts are sanity-checked, not asserted.
"""

from __future__ import annotations

import json

from lib import doctor


# ── _scan_hook_scripts ───────────────────────────────────────

def test_scan_hook_scripts_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "os.path.expanduser",
        lambda p: str(tmp_path / "no-settings.json") if "settings.json" in p else p,
    )
    assert doctor._scan_hook_scripts() == []


def test_scan_hook_scripts_malformed_json(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text("{not valid")
    _patch_settings_path(monkeypatch, settings)
    assert doctor._scan_hook_scripts() == []


def test_scan_hook_scripts_empty_hooks(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {}}))
    _patch_settings_path(monkeypatch, settings)
    assert doctor._scan_hook_scripts() == []


def test_scan_hook_scripts_finds_python_script(tmp_path, monkeypatch):
    script = tmp_path / "my_hook.py"
    script.write_text("# hook\n")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "PostToolUse": [
                {"hooks": [{"command": f"python {script}"}]},
            ],
        },
    }))
    _patch_settings_path(monkeypatch, settings)
    scanned = doctor._scan_hook_scripts()
    assert len(scanned) == 1
    assert scanned[0]["event"] == "PostToolUse"
    assert scanned[0]["script"] == str(script)
    assert scanned[0]["present"] is True


def test_scan_hook_scripts_marks_missing_script(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "PostToolUse": [
                {"hooks": [{"command": "python /gone/never_existed.py"}]},
            ],
        },
    }))
    _patch_settings_path(monkeypatch, settings)
    scanned = doctor._scan_hook_scripts()
    assert len(scanned) == 1
    assert scanned[0]["present"] is False


def test_scan_hook_scripts_skips_module_invocation(tmp_path, monkeypatch):
    """Hooks using `-m hook_manager` have no concrete path to check."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "PostToolUse": [
                {"hooks": [{"command": "python -m hook_manager PostToolUse"}]},
            ],
        },
    }))
    _patch_settings_path(monkeypatch, settings)
    assert doctor._scan_hook_scripts() == []


# ── _which ──────────────────────────────────────────────────

def test_which_returns_path_for_existing_binary():
    # 'python3' or 'python' should reliably exist on any dev machine
    # where pytest can even start.
    result = doctor._which("python3") or doctor._which("python")
    assert result is not None


def test_which_none_for_nonexistent_binary():
    assert doctor._which("definitely-not-installed-xyz") is None


# ── run_checks shape ────────────────────────────────────────

def test_run_checks_returns_grouped_shape():
    result = doctor.run_checks()
    assert "groups" in result
    assert "project" in result
    group_names = [g["name"] for g in result["groups"]]
    assert "Core tools" in group_names
    # Every group has an items list.
    for g in result["groups"]:
        assert isinstance(g["items"], list)


def test_run_checks_project_section_has_expected_flags():
    result = doctor.run_checks()
    proj_ids = {i["id"] for i in result["project"]["items"]}
    expected = {"venv", "node_modules", "settings_local", "web_ui",
                "sqlite_db", "mysql_configured"}
    assert expected.issubset(proj_ids)


def test_topic_sync_items_empty_when_no_repos(fake_git_repo):
    """The doctor group only appears when there are registered repos —
    solo users who haven't run `add-repo` shouldn't see empty noise.
    `fake_git_repo` gives us a clean DB via the tmp_db fixture chain."""
    assert doctor._topic_sync_items() == []


def test_topic_sync_items_flags_disk_drift(fake_git_repo):
    """Simulates a `git pull` that left disk newer than the local
    snapshot — doctor must produce a warning row with an actionable
    hint pointing at `regin topics import`.

    Seeds the snapshot via `load_authoritative_graph` (synchronous,
    no background indexer) instead of `apply_diff` to avoid racing
    with the `_bg_reindex` thread `apply_diff` spawns.
    """
    from lib.topics import bootstrap
    from lib.topics.core import load_graph, write_split_graph
    from lib.topics.graph_io import load_authoritative_graph
    from lib.topics.snapshots import resolve_or_create_repo

    bootstrap(fake_git_repo)
    resolve_or_create_repo(str(fake_git_repo))
    # Seed the initial GraphSnapshot synchronously.
    load_authoritative_graph(str(fake_git_repo))

    # Simulate an upstream change landing in the graph without an apply.
    disk = load_graph(fake_git_repo)
    disk["topics"]["y"] = {
        "label": "Y", "intent": "y", "status": "active",
        "aliases": [], "refs": [], "edges": [],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    write_split_graph(fake_git_repo, disk)

    items = doctor._topic_sync_items()
    assert len(items) == 1
    item = items[0]
    assert item["present"] is False, f"expected drift warning, got: {item}"
    assert item["optional"] is True
    assert "regin topics import" in item["install_hint"]


def test_run_checks_core_tools_has_git():
    # python is intentionally absent from doctor — regin runs in python, so
    # detecting it is tautological. Core tools surfaces the non-tautological
    # essentials (currently just git).
    result = doctor.run_checks()
    core = next(g for g in result["groups"] if g["name"] == "Core tools")
    ids = {i["id"] for i in core["items"]}
    assert ids == {"git"}


# ── _scan_hook_scripts edge branches ────────────────────────

def test_scan_hook_scripts_skips_non_list_entries(tmp_path, monkeypatch):
    """When an event's entries is a string/dict, not a list, it is skipped."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "PostToolUse": "not-a-list",
            "PreToolUse": [
                {"hooks": [{"command": "python /tmp/real.py"}]},
            ],
        },
    }))
    _patch_settings_path(monkeypatch, settings)
    scanned = doctor._scan_hook_scripts()
    # Only the list-shaped event contributes a row.
    assert [r["event"] for r in scanned] == ["PreToolUse"]


def test_scan_hook_scripts_skips_commands_with_no_script_path(
        tmp_path, monkeypatch):
    """Commands that don't match the /foo.py|sh regex are skipped."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "PostToolUse": [
                {"hooks": [{"command": "echo hello world"}]},
                {"hooks": [{"command": "curl https://x"}]},
            ],
        },
    }))
    _patch_settings_path(monkeypatch, settings)
    assert doctor._scan_hook_scripts() == []


# ── _version exception path ─────────────────────────────────

def test_version_swallows_subprocess_exceptions(monkeypatch):
    """Any exception during version lookup → empty string."""
    import subprocess as _sp

    def _boom(*_a, **_kw):
        raise _sp.TimeoutExpired(cmd="git", timeout=5)

    monkeypatch.setattr(doctor.subprocess, "run", _boom)
    assert doctor._version(["git", "--version"]) == ""


def test_version_returns_first_nonempty_line(monkeypatch):
    class _Res:
        stdout = "v1.2.3\nextra\n"
        stderr = ""
    monkeypatch.setattr(
        doctor.subprocess, "run",
        lambda *_a, **_kw: _Res(),
    )
    assert doctor._version(["x", "--version"]) == "v1.2.3"


# ── _check_playwright branches ──────────────────────────────

def test_run_checks_playwright_direct_binary(monkeypatch):
    """When `playwright` is on PATH, _check_playwright returns its info."""
    def _which(name):
        return "/fake/bin/playwright" if name == "playwright" else None

    monkeypatch.setattr(doctor, "_which", _which)
    monkeypatch.setattr(doctor, "_version",
                         lambda cmd: "Version 1.50.0"
                         if cmd[:2] == ["playwright", "--version"] else "")
    result = doctor.run_checks()
    frontend = next(g for g in result["groups"]
                    if g["name"] == "Frontend (Node.js)")
    pw = next(i for i in frontend["items"] if i["id"] == "playwright")
    assert pw["present"] is True
    assert pw["path"] == "/fake/bin/playwright"
    assert pw["version"] == "Version 1.50.0"


def test_run_checks_playwright_via_npx(monkeypatch):
    """No direct binary; npx is present and reports playwright."""
    def _which(name):
        return "/fake/bin/npx" if name == "npx" else None

    class _OkRun:
        returncode = 0
        stdout = "Version 1.50.0\n"
        stderr = ""

    monkeypatch.setattr(doctor, "_which", _which)
    monkeypatch.setattr(doctor.subprocess, "run",
                         lambda *_a, **_kw: _OkRun())
    result = doctor.run_checks()
    frontend = next(g for g in result["groups"]
                    if g["name"] == "Frontend (Node.js)")
    pw = next(i for i in frontend["items"] if i["id"] == "playwright")
    assert pw["present"] is True
    # path is None for the npx path.
    assert pw["path"] is None


def test_run_checks_playwright_npx_timeout_swallowed(monkeypatch):
    """npx subprocess times out → treated as absent, no crash."""
    import subprocess as _sp

    def _which(name):
        return "/fake/bin/npx" if name == "npx" else None

    def _boom(*_a, **_kw):
        raise _sp.TimeoutExpired(cmd="npx", timeout=10)

    monkeypatch.setattr(doctor, "_which", _which)
    monkeypatch.setattr(doctor.subprocess, "run", _boom)
    result = doctor.run_checks()
    frontend = next(g for g in result["groups"]
                    if g["name"] == "Frontend (Node.js)")
    pw = next(i for i in frontend["items"] if i["id"] == "playwright")
    assert pw["present"] is False


def test_run_checks_playwright_npx_file_not_found_swallowed(monkeypatch):
    """npx throws FileNotFoundError → treated as absent, no crash."""
    def _which(name):
        return "/fake/bin/npx" if name == "npx" else None

    def _boom(*_a, **_kw):
        raise FileNotFoundError("npx not found")

    monkeypatch.setattr(doctor, "_which", _which)
    monkeypatch.setattr(doctor.subprocess, "run", _boom)
    result = doctor.run_checks()
    frontend = next(g for g in result["groups"]
                    if g["name"] == "Frontend (Node.js)")
    pw = next(i for i in frontend["items"] if i["id"] == "playwright")
    assert pw["present"] is False


# ── mysql_ok shared-mode branch ─────────────────────────────

def test_run_checks_mysql_ok_shared_mode_reports_configured(monkeypatch):
    """MODE=shared → mysql_ok depends on is_configured() from mysql_db."""
    from lib import settings as _cfg
    monkeypatch.setattr(_cfg.settings, "mode", "shared")

    from lib import mysql_db
    monkeypatch.setattr(mysql_db, "is_configured", lambda: True)

    result = doctor.run_checks()
    mysql = next(i for i in result["project"]["items"]
                 if i["id"] == "mysql_configured")
    assert mysql["present"] is True


def test_run_checks_mysql_ok_exception_is_false(monkeypatch):
    """Any exception during MODE/mysql_db lookup → mysql_ok False."""
    import builtins as _bi
    real_import = _bi.__import__

    def _broken_import(name, *args, **kwargs):
        if name == "lib.settings":
            raise RuntimeError("simulated import failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(_bi, "__import__", _broken_import)
    result = doctor.run_checks()
    mysql = next(i for i in result["project"]["items"]
                 if i["id"] == "mysql_configured")
    assert mysql["present"] is False


# ── helper ──────────────────────────────────────────────────

def _patch_settings_path(monkeypatch, settings_file):
    real_expanduser = __import__("os.path", fromlist=["expanduser"]).expanduser

    def _redir(p):
        if "settings.json" in p:
            return str(settings_file)
        return real_expanduser(p)

    monkeypatch.setattr("os.path.expanduser", _redir)


# ── _topics_pending_promote_items ────────────────────────────

def test_topics_pending_items_empty_when_no_repos(fake_git_repo):
    assert doctor._topics_pending_promote_items() == []


def test_topics_pending_items_clean_when_no_overlay(fake_git_repo):
    from lib.topics.snapshots import resolve_or_create_repo

    resolve_or_create_repo(str(fake_git_repo))
    items = doctor._topics_pending_promote_items()
    assert len(items) == 1
    assert items[0]["present"] is True
    assert items[0]["version"] == "nothing pending"


def test_topics_pending_items_warn_with_count_and_hint(fake_git_repo):
    """Two overlay-added topics plus one tombstone → count 3 and a
    `topics promote --all` hint."""
    from lib.topics.core import save_local_graph
    from lib.topics.snapshots import resolve_or_create_repo

    resolve_or_create_repo(str(fake_git_repo))
    save_local_graph(fake_git_repo, {
        "topics": {"x": {"label": "X"}, "y": {"label": "Y"}},
        "deleted_topics": ["gone"],
    })

    items = doctor._topics_pending_promote_items()
    assert len(items) == 1
    item = items[0]
    assert item["present"] is False
    assert item["optional"] is True
    assert "3 local topic change(s)" in item["install_hint"]
    assert "regin topics promote --all" in item["install_hint"]


def test_topics_pending_items_warn_on_unreadable_overlay(fake_git_repo):
    from lib.topics.core import topic_local_path
    from lib.topics.snapshots import resolve_or_create_repo

    resolve_or_create_repo(str(fake_git_repo))
    path = topic_local_path(fake_git_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json")

    items = doctor._topics_pending_promote_items()
    assert len(items) == 1
    assert items[0]["present"] is False
    assert "overlay read failed" in items[0]["install_hint"]


# ── _stale_skeleton_items ────────────────────────────────────

def _fake_surface(surface_id, body):
    from types import SimpleNamespace
    return SimpleNamespace(id=surface_id, default_body=lambda: body)


def test_stale_skeleton_items_ok_when_seed_matches_default(monkeypatch):
    from lib.prompt_templates import create_template

    create_template({"slug": "surf-a", "label": "Surf A", "body": "hello"})
    monkeypatch.setattr("lib.prompts.registry.list_surfaces",
                        lambda: [_fake_surface("surf-a", "hello")])

    items = doctor._stale_skeleton_items()
    assert len(items) == 1
    assert items[0]["present"] is True
    assert items[0]["version"] == "1 match built-in defaults"


def test_stale_skeleton_items_warn_on_diverged_seed(monkeypatch):
    """A seeded row whose body drifted from the registered default fires;
    a surface with no row yet (never seeded) is skipped, not reported."""
    from lib.prompt_templates import create_template

    create_template({"slug": "surf-a", "label": "Surf A", "body": "old seed"})
    monkeypatch.setattr("lib.prompts.registry.list_surfaces",
                        lambda: [_fake_surface("surf-a", "new default"),
                                 _fake_surface("unseeded", "x")])

    items = doctor._stale_skeleton_items()
    assert len(items) == 1
    item = items[0]
    assert item["label"] == "surf-a"
    assert item["present"] is False
    assert item["optional"] is True
    assert "stale seed or intentional edit" in item["install_hint"]
    assert "reset_skeleton_to_default('surf-a')" in item["install_hint"]


# ── _agent_bridge_items ──────────────────────────────────────

def test_agent_bridge_items_disabled_single_optional_row(monkeypatch):
    monkeypatch.setattr(doctor._settings.agent_bridge, "enabled", False)
    items = doctor._agent_bridge_items(doctor._check_tool)
    assert [i["id"] for i in items] == ["bridge_enabled"]
    assert items[0]["present"] is False
    assert items[0]["optional"] is True
    assert "settings.local.json" in items[0]["install_hint"]


def test_agent_bridge_items_enabled_reports_all_conditions(monkeypatch):
    monkeypatch.setattr(doctor._settings.agent_bridge, "enabled", True)
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.setattr(
        "lib.agent_bridge.store.list_reachable_sessions",
        lambda: [{"trace_id": "t1"}, {"trace_id": "t2"}],
    )
    items = {i["id"]: i for i in doctor._agent_bridge_items(doctor._check_tool)}
    assert items["bridge_enabled"]["present"] is True
    assert items["bridge_env"]["present"] is True
    assert items["bridge_panes"]["present"] is True
    assert items["bridge_panes"]["version"] == "2 registered"
    assert "bridge_tmux" in items


def test_agent_bridge_items_env_unset_and_no_panes(monkeypatch):
    monkeypatch.setattr(doctor._settings.agent_bridge, "enabled", True)
    monkeypatch.delenv("REGIN_BRIDGE", raising=False)
    monkeypatch.setattr("lib.agent_bridge.store.list_reachable_sessions", lambda: [])
    items = {i["id"]: i for i in doctor._agent_bridge_items(doctor._check_tool)}
    assert items["bridge_env"]["present"] is False
    assert items["bridge_panes"]["present"] is False
    assert items["bridge_panes"]["version"] == "0 registered"
