"""Agent-bridge slash-command / skill accept list (`lib/agent_bridge/commands.py`).

Powers the /live composer's `/`-autocomplete. These pin the enumeration
contract against a temp `.claude/` tree:

  * project scope resolves from the pane registry's `cwd` (walk up to the
    nearest `.claude/`), user scope from `~/.claude/`,
  * commands come from `commands/**/*.md` (nested dirs namespaced `a:b`),
    skills from `skills/*/SKILL.md`,
  * description = frontmatter `description`, else the first prose line,
  * project shadows a same-named user entry; sort is command-before-skill
    then name; `_`-prefixed entries and missing dirs are no-ops,
  * unknown cwd falls back to `settings.project_root`; nothing ever raises.

`store.get_pane_cwd` is monkeypatched (no DB); `HOME` is repointed at a temp
dir so the user scan is hermetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.agent_bridge import commands
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
    monkeypatch.setattr(commands.store, "get_pane_cwd", lambda tid: str(project))
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


def test_unknown_cwd_falls_back_to_project_root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write(home / ".claude" / "skills" / "only-user" / "SKILL.md",
           "---\ndescription: u\n---\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(commands.store, "get_pane_cwd", lambda tid: None)
    monkeypatch.setattr(settings, "project_root", tmp_path / "nowhere")
    rows = commands.list_session_commands("t1")
    # No project .claude/ → only the user skill; never raises.
    assert _by_name(rows).keys() == {"only-user"}
