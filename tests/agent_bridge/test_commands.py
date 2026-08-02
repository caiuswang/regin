"""Agent-bridge slash-command / skill accept list (`lib/agent_bridge/commands.py`).

Powers the /live composer's `/`-autocomplete. Two layers are pinned here.

The AUTHORITATIVE layer is the Claude Agent SDK handshake in the session's own
cwd — the same catalog the raw terminal offers there:

  * the handshake happens in that cwd, NOT in whatever `.claude/` ancestor sits
    above it and not in regin's root: a session gets the commands it can
    actually run,
  * when it answers, its rows ARE the list (built-ins and plugin commands the
    filesystem scan can't see; scan-only names are not bolted on),
  * provenance is derived by cross-referencing the scan first — a scanned name
    keeps its kind + project/user scope even when it is `a:b` (nested project
    dirs are namespaced that way too); only an unscanned `a:b` is a plugin,
    and the rest are built-ins,
  * a name the SDK reports twice (a user and a project variant) resolves to
    the variant whose scope the badge claims,
  * the SDK's `aliases` (`/new` → `clear`) ride along on the row so the menu
    can match them, while the canonical name stays what gets inserted,
  * `clear` / `exit` / `logout` carry risk=destructive only as BUILT-INS — a
    project command named `clear` is the session's own, not a state wipe,
  * the per-root result is TTL-cached behind a per-root lock, so a second list
    (or a concurrent one) costs no extra handshake,
  * ANY failure — raise or timeout — degrades to the scan, never an exception.

The DEGRADED layer is that filesystem scan, pinned against a temp `.claude/`
tree (`conftest.py` stubs the handshake out for every test in this dir):

  * project scope resolves from the pane registry's `cwd` (walk up to the
    nearest `.claude/`), user scope from `~/.claude/`,
  * commands come from `commands/**/*.md` (nested dirs namespaced `a:b`),
    skills from `skills/*/SKILL.md`, workflows from `workflows/*.js`,
  * description = frontmatter `description`, else the first prose line,
  * project shadows a same-named user entry; sort is command-before-skill
    then name; `_`-prefixed entries and missing dirs are no-ops,
  * a cwd with no `.claude/` ancestor has NO project scope — regin's own tree
    is not stood in for it, or rows that are really `~/.claude/` ones badge
    `project`; nothing ever raises.

And the whole surface is fail-closed on the cwd itself: an unregistered trace
id, a NULL cwd and a cwd that no longer exists all get an EMPTY catalog, the
same as `bridge-files` — never regin's own command list.

`store.get_pane_cwd` is monkeypatched (no DB); `HOME` is repointed at a temp
dir so the user scan is hermetic.
"""

from __future__ import annotations

import sys
import threading
import types
from pathlib import Path

import anyio
import pytest

from lib.agent_bridge import commands, roots
from lib.agent_bridge.commands import _server_info_commands as _real_server_info
from lib.settings import settings


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A project + user `.claude/` tree; returns (project_dir, names->row)."""
    project = tmp_path / "proj"
    home = tmp_path / "home"
    # Project slash command with no frontmatter — description is first prose.
    _write(project / ".claude" / "commands" / "deploy.md",
           "# Deploy Command\n\nShip the current branch to prod.\n")
    # Nested command → `a:b` namespacing; `_`-prefixed sibling is skipped.
    _write(project / ".claude" / "commands" / "spec" / "create.md",
           "Create a spec.\n")
    _write(project / ".claude" / "commands" / "_hidden.md", "nope\n")
    # Project skill with frontmatter description.
    _write(project / ".claude" / "skills" / "lint" / "SKILL.md",
           "---\nname: lint\ndescription: Lint the tree.\n---\n# Lint\n")
    # User scope: one unique skill + one that collides with a project name.
    _write(home / ".claude" / "skills" / "userskill" / "SKILL.md",
           "---\ndescription: A user skill.\n---\n")
    _write(home / ".claude" / "commands" / "deploy.md",
           "---\ndescription: USER deploy (should be shadowed).\n---\n")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(roots.store, "get_pane_cwd", lambda tid: str(project))
    return project


def _by_name(rows):
    return {r["name"]: r for r in rows}


def test_enumerates_project_and_user(tree):
    rows = commands.list_session_commands("t1")
    names = _by_name(rows)
    assert names["deploy"]["kind"] == "command"
    assert names["spec:create"]["kind"] == "command"   # nested namespacing
    assert names["lint"]["kind"] == "skill"
    assert names["userskill"]["scope"] == "user"


def test_description_frontmatter_and_prose(tree):
    names = _by_name(commands.list_session_commands("t1"))
    # No-frontmatter command → first prose line, not the `# H1`.
    assert names["deploy"]["description"] == "Ship the current branch to prod."
    assert names["lint"]["description"] == "Lint the tree."


def test_project_shadows_user_and_scope(tree):
    names = _by_name(commands.list_session_commands("t1"))
    # `deploy` exists in both scopes; the project one wins.
    assert names["deploy"]["scope"] == "project"
    assert "should be shadowed" not in names["deploy"]["description"]


def test_hidden_and_missing_are_noops(tree):
    names = _by_name(commands.list_session_commands("t1"))
    assert "_hidden" not in names            # `_`-prefixed skipped
    assert "hidden" not in names


def test_sort_command_before_skill(tree):
    rows = commands.list_session_commands("t1")
    kinds = [r["kind"] for r in rows]
    # All commands precede all skills.
    assert kinds == sorted(kinds, key=lambda k: 0 if k == "command" else 1)
    assert kinds.index("command") < kinds.index("skill")


def test_scanned_rows_carry_the_extended_shape(tree):
    """Fallback rows keep the SDK-era keys so the UI has one row shape."""
    row = _by_name(commands.list_session_commands("t1"))["deploy"]
    assert row["argumentHint"] == ""
    assert row["risk"] is None


def test_workflows_are_scanned_and_not_badged_builtin(tree):
    """`.claude/workflows/*.js` are offered after a `/` like any command, so a
    scan that skips them badges them `builtin` (the unknown-name fallback)."""
    _write(tree / ".claude" / "workflows" / "vue-refactor.js",
           "export const meta = {\n  name: 'vue-refactor',\n"
           "  description: 'Refactor the views.',\n}\n")
    _write(tree / ".claude" / "workflows" / "_helper.js", "// skipped\n")
    row = _by_name(commands.list_session_commands("t1"))["vue-refactor"]
    assert (row["kind"], row["scope"]) == ("workflow", "project")
    assert row["description"] == "Refactor the views."
    assert "_helper" not in _by_name(commands.list_session_commands("t1"))


def test_sdk_workflow_row_badges_workflow_not_builtin(tree, monkeypatch):
    _write(tree / ".claude" / "workflows" / "vue-refactor.js",
           "export const meta = { description: 'x' }\n")
    _stub_sdk(monkeypatch, [{"name": "vue-refactor", "description": "SDK wf"}])
    row = _by_name(commands.list_session_commands("t1"))["vue-refactor"]
    assert (row["kind"], row["scope"]) == ("workflow", "project")
    assert row["risk"] is None


def test_unparseable_workflow_meta_degrades_to_no_description(tree):
    _write(tree / ".claude" / "workflows" / "opaque.js", "export default 1\n")
    assert _by_name(commands.list_session_commands("t1"))[
        "opaque"]["description"] == ""


# ── no cwd, no catalog (parity with bridge-files) ────────────


def test_unregistered_cwd_yields_no_catalog(tmp_path, monkeypatch):
    """A trace id the registry doesn't know gets NOTHING — not regin's own
    catalog. Substituting `settings.project_root` here handed an arbitrary id
    this host's local command and skill names, with full descriptions."""
    home = tmp_path / "home"
    _write(home / ".claude" / "skills" / "only-user" / "SKILL.md",
           "---\ndescription: u\n---\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(roots.store, "get_pane_cwd", lambda tid: None)
    calls = _stub_sdk(monkeypatch)
    assert commands.list_session_commands("t-ghost") == []
    assert calls == []                       # no handshake anywhere, either
    # And the fallback that used to be taken is a real, populated tree.
    assert Path(settings.project_root).is_dir()


def test_vanished_cwd_yields_no_catalog(tmp_path, monkeypatch):
    """A registry row outlives the directory it names (a removed worktree)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(roots.store, "get_pane_cwd",
                        lambda tid: str(tmp_path / "gone"))
    calls = _stub_sdk(monkeypatch)
    assert commands.list_session_commands("t-dead") == []
    assert calls == []


def test_cwd_without_a_claude_ancestor_has_no_project_scope(tmp_path, monkeypatch):
    """Provenance must not fall back to regin's tree: a row that is genuinely
    a `~/.claude/` one badged `project` is a lie the composer renders."""
    home = tmp_path / "home"
    _write(home / ".claude" / "skills" / "base-skill" / "SKILL.md",
           "---\ndescription: u\n---\n")
    plain = tmp_path / "noclaude"
    plain.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(settings, "project_root", tmp_path / "regin")
    _write(tmp_path / "regin" / ".claude" / "skills" / "base-skill" / "SKILL.md",
           "---\ndescription: regin's own\n---\n")
    monkeypatch.setattr(roots.store, "get_pane_cwd", lambda tid: str(plain))
    _stub_sdk(monkeypatch, [{"name": "base-skill", "description": "s"}])
    row = _by_name(commands.list_session_commands("t1"))["base-skill"]
    assert (row["kind"], row["scope"]) == ("skill", "user")


def test_project_root_is_none_without_a_claude_ancestor(tmp_path):
    plain = tmp_path / "noclaude"
    plain.mkdir(parents=True)
    assert roots.project_root(plain) is None
    assert roots.project_root(None) is None


# ── SDK layer: the authoritative catalog + its fallbacks ─────

_SDK_ENTRIES = [
    {"name": "clear", "description": "Start a new session",
     "argumentHint": "[name]"},
    {"name": "help", "description": "Show help", "argumentHint": ""},
    {"name": "exit", "description": "Exit", "argumentHint": ""},
    {"name": "logout", "description": "Sign out", "argumentHint": ""},
    {"name": "pack:build", "description": "A plugin command",
     "argumentHint": "<target>"},
    {"name": "deploy", "description": "SDK deploy", "argumentHint": "<env>"},
    {"name": "lint", "description": "SDK lint", "argumentHint": ""},
    {"name": "userskill", "description": "SDK user skill", "argumentHint": ""},
]


def _stub_sdk(monkeypatch, entries=_SDK_ENTRIES):
    """Patch the handshake with a counting stub; returns the call log."""
    calls: list[str] = []

    async def _fake(root):
        calls.append(root)
        return [dict(entry) for entry in entries]

    monkeypatch.setattr(commands, "_server_info_commands", _fake)
    commands._sdk_cache.clear()
    return calls


def test_sdk_answer_is_the_list(tree, monkeypatch):
    """The SDK rows ARE the catalog — scan-only names are not bolted on."""
    calls = _stub_sdk(monkeypatch)
    names = _by_name(commands.list_session_commands("t1"))
    assert calls == [str(tree)]              # handshake ran in the session's cwd
    assert set(names) == {e["name"] for e in _SDK_ENTRIES}
    assert "spec:create" not in names        # scan-only, absent from the SDK
    assert names["deploy"]["description"] == "SDK deploy"
    assert names["deploy"]["argumentHint"] == "<env>"


def test_sdk_provenance_all_four_kinds(tree, monkeypatch):
    _stub_sdk(monkeypatch)
    names = _by_name(commands.list_session_commands("t1"))
    assert (names["deploy"]["kind"], names["deploy"]["scope"]) == (
        "command", "project")            # scanned project command
    assert (names["lint"]["kind"], names["lint"]["scope"]) == (
        "skill", "project")              # scanned project skill
    assert (names["userskill"]["kind"], names["userskill"]["scope"]) == (
        "skill", "user")                 # scanned only under ~/.claude
    assert (names["pack:build"]["kind"], names["pack:build"]["scope"]) == (
        "plugin", "plugin")              # `a:b` namespacing
    assert (names["help"]["kind"], names["help"]["scope"]) == (
        "builtin", "builtin")            # unknown to the scan


def test_sdk_provenance_prefers_the_scan_over_the_colon_heuristic(tree, monkeypatch):
    """A nested PROJECT command is namespaced `a:b` exactly like a plugin —
    the scan knows which it is, so it is asked first."""
    _stub_sdk(monkeypatch, [{"name": "spec:create", "description": "SDK spec"},
                            {"name": "pack:build", "description": "plugin"}])
    names = _by_name(commands.list_session_commands("t1"))
    assert (names["spec:create"]["kind"], names["spec:create"]["scope"]) == (
        "command", "project")            # .claude/commands/spec/create.md
    assert (names["pack:build"]["kind"], names["pack:build"]["scope"]) == (
        "plugin", "plugin")              # unknown to either scan


def test_sdk_shadowed_name_resolves_to_the_project_variant(tree, monkeypatch):
    """The SDK lists a shadowed name twice, distinguished only by a scope
    suffix. The terminal runs the project one, so the row must be the project
    one — a `(user)` description under a `project` badge is a lie."""
    _stub_sdk(monkeypatch, [
        {"name": "deploy", "description": "deploy - guide from x (user)"},
        {"name": "deploy", "description": "deploy - guide from x (project)"}])
    rows = commands.list_session_commands("t1")
    assert [r["name"] for r in rows] == ["deploy"]
    assert rows[0]["scope"] == "project"
    assert rows[0]["description"].endswith("(project)")


def test_sdk_aliases_are_carried_through(tree, monkeypatch):
    """The terminal accepts `/new` for `/clear`; a menu that drops the SDK's
    `aliases` answers "no command matches" for a name that works."""
    _stub_sdk(monkeypatch, [
        {"name": "clear", "description": "Start a new session",
         "aliases": ["reset", "new"]},
        {"name": "usage", "description": "Show cost", "aliases": ["cost"]},
        {"name": "help", "description": "Show help"},
        {"name": "junk", "description": "d", "aliases": ["ok", "", 7, None]},
        {"name": "wrong", "description": "d", "aliases": "notalist"},
    ])
    names = _by_name(commands.list_session_commands("t1"))
    assert names["clear"]["aliases"] == ["reset", "new"]
    assert names["usage"]["aliases"] == ["cost"]
    assert names["help"]["aliases"] == []          # absent → empty, not missing
    assert names["junk"]["aliases"] == ["ok"]      # non-string entries dropped
    assert names["wrong"]["aliases"] == []         # non-list payload ignored
    # The canonical name is still what the row inserts.
    assert names["clear"]["name"] == "clear"


def test_scanned_rows_carry_an_empty_alias_list(tree):
    """One row shape across both paths — the scan has no aliases to report."""
    assert _by_name(commands.list_session_commands("t1"))[
        "deploy"]["aliases"] == []


def test_sdk_risk_flag_only_on_destructive_builtins(tree, monkeypatch):
    _stub_sdk(monkeypatch)
    names = _by_name(commands.list_session_commands("t1"))
    flagged = {n for n, row in names.items() if row["risk"] == "destructive"}
    assert flagged == {"clear", "exit", "logout"}
    assert all(row["risk"] is None for n, row in names.items()
               if n not in flagged)


def test_project_command_named_clear_is_not_flagged_destructive(tmp_path, monkeypatch):
    """`risk` describes the BUILT-IN `/clear`, not any command sharing its
    name: a project `clear.md` wipes nothing."""
    project = tmp_path / "proj"
    _write(project / ".claude" / "commands" / "clear.md", "Clear the queue.\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(roots.store, "get_pane_cwd", lambda tid: str(project))
    _stub_sdk(monkeypatch, [{"name": "clear", "description": "Clear the queue."}])
    row = _by_name(commands.list_session_commands("t1"))["clear"]
    assert (row["kind"], row["scope"]) == ("command", "project")
    assert row["risk"] is None


def test_sdk_order_builtins_last_then_kind_then_name(tree, monkeypatch):
    _stub_sdk(monkeypatch)
    rows = commands.list_session_commands("t1")
    assert [r["name"] for r in rows] == [
        "deploy",                    # command
        "pack:build",                # plugin
        "lint", "userskill",         # skills, by name
        "clear", "exit", "help", "logout",   # built-ins last, by name
    ]


def test_sdk_handshake_runs_in_the_cwd_not_a_claude_ancestor(tmp_path, monkeypatch):
    """A session whose cwd has no `.claude/` above it still gets ITS catalog.

    Substituting regin's root here is what handed such a session 37 commands
    that do not exist where it runs.
    """
    plain = tmp_path / "plainproj"
    plain.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(settings, "project_root", tmp_path / "regin")
    monkeypatch.setattr(roots.store, "get_pane_cwd", lambda tid: str(plain))
    calls = _stub_sdk(monkeypatch, [{"name": "help", "description": "h"}])
    commands.list_session_commands("t1")
    assert calls == [str(plain)]


def test_sdk_result_is_cached_per_root(tree, monkeypatch):
    """The ~3s handshake happens once per root, not once per request."""
    calls = _stub_sdk(monkeypatch)
    commands.list_session_commands("t1")
    commands.list_session_commands("t1")
    assert calls == [str(tree)]


def test_concurrent_cold_requests_handshake_once(tree, monkeypatch):
    """N simultaneous cold requests must spawn one `claude`, not N."""
    calls: list[str] = []

    async def _slow(root):
        calls.append(root)
        await anyio.sleep(0.2)
        return [dict(entry) for entry in _SDK_ENTRIES]

    monkeypatch.setattr(commands, "_server_info_commands", _slow)
    commands._sdk_cache.clear()
    threads = [threading.Thread(target=commands.list_session_commands,
                                args=("t1",)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls == [str(tree)]


def test_sdk_dedups_repeated_names(tree, monkeypatch):
    _stub_sdk(monkeypatch, [{"name": "dup", "description": "first"},
                            {"name": "dup", "description": "second"}])
    rows = commands.list_session_commands("t1")
    assert [r["name"] for r in rows] == ["dup"]
    assert rows[0]["description"] == "first"


def test_sdk_failure_falls_back_to_scan(tree, monkeypatch):
    async def _boom(root):
        raise RuntimeError("no claude binary")

    monkeypatch.setattr(commands, "_server_info_commands", _boom)
    commands._sdk_cache.clear()
    names = _by_name(commands.list_session_commands("t1"))
    # The scan's own catalog, never an exception.
    assert names.keys() == {"deploy", "spec:create", "lint", "userskill"}
    assert names["deploy"]["description"] == "Ship the current branch to prod."


def test_sdk_timeout_falls_back_to_scan(tree, monkeypatch):
    """A hung handshake can't wedge the request — fail_after cuts it loose."""
    import anyio

    async def _hang(root):
        await anyio.sleep(30)
        return _SDK_ENTRIES

    monkeypatch.setattr(commands, "_server_info_commands", _hang)
    monkeypatch.setattr(commands, "SDK_TIMEOUT_SECONDS", 0.05)
    commands._sdk_cache.clear()
    names = _by_name(commands.list_session_commands("t1"))
    assert names.keys() == {"deploy", "spec:create", "lint", "userskill"}


def test_sdk_empty_result_falls_back_to_scan(tree, monkeypatch):
    _stub_sdk(monkeypatch, [])
    names = _by_name(commands.list_session_commands("t1"))
    assert names.keys() == {"deploy", "spec:create", "lint", "userskill"}


# ── the handshake body itself, against a fake SDK module ─────
#
# Every test above stubs `_server_info_commands` out, so the one function that
# actually talks to the SDK would otherwise never run. This exercises its real
# body with `claude_agent_sdk` swapped for a fake — no `claude` is spawned.


def _fake_sdk_module(payload):
    """A stand-in `claude_agent_sdk`; returns (module, constructed options)."""
    built: list[dict] = []

    class _Options:
        def __init__(self, **kwargs):
            built.append(kwargs)
            self.kwargs = kwargs

    class _Client:
        def __init__(self, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            built.append({"closed": True})
            return False

        async def get_server_info(self):
            return payload

    return types.SimpleNamespace(ClaudeAgentOptions=_Options,
                                 ClaudeSDKClient=_Client), built


def test_server_info_commands_reads_the_payload(monkeypatch):
    module, built = _fake_sdk_module({"commands": [
        {"name": "help", "description": "Show help"},
        {"name": "", "description": "nameless — dropped"},
        "not-a-dict",
    ]})
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)
    rows = anyio.run(_real_server_info, "/some/where")
    assert rows == [{"name": "help", "description": "Show help"}]
    assert built[0] == {"cwd": "/some/where"}   # handshake runs in that cwd
    assert built[-1] == {"closed": True}        # client always torn down


@pytest.mark.parametrize("payload", [None, {}, {"commands": None}])
def test_server_info_commands_tolerates_an_empty_payload(monkeypatch, payload):
    module, _ = _fake_sdk_module(payload)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)
    assert anyio.run(_real_server_info, "/some/where") == []
