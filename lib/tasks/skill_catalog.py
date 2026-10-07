"""The skills a run launched in one directory can actually resolve.

The arm sheet used to offer regin's own deployed skills, which says nothing
about the repo a task runs in: a skill deployed only to repo A, armed on a task
for repo B, opens its run with a slash command nobody answers. So the menu is
read off disk the way the armed provider's CLI resolves it at launch — the
project's config dirs from `cwd` up to its checkout root, the same dirs under
home, and (for Claude Code) the plugins enabled for that project. Which dirs
those are is the provider's `skill_catalog_layout()`.

Best-effort by design: a name missing here can still be typed into the sheet,
so an unreadable file or a plugin layout this misses narrows the menu, never
the arm.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from lib.providers.base import SkillCatalogLayout

PROJECT, USER, PLUGIN = "project", "user", "plugin"


def _names_in(base: Path, layout: SkillCatalogLayout) -> list[str]:
    names = []
    for parts in layout.skill_dirs:
        skills = base.joinpath(*parts)
        if skills.is_dir():
            names += [d.name for d in skills.iterdir()
                      if (d / "SKILL.md").is_file()]
    for parts in layout.command_dirs:
        commands = base.joinpath(*parts)
        if commands.is_dir():
            names += [f.stem for f in commands.glob("*.md") if f.is_file()]
    return names


_PLUGIN_LAYOUT = SkillCatalogLayout(skill_dirs=(("skills",),),
                                    command_dirs=(("commands",),))


def _project_dirs(cwd: Path, home: Path) -> list[Path]:
    """`cwd` and its ancestors up to the checkout root, nearest first.

    Home is never a project: its `.claude/` is the user scope, listed as such.
    """
    dirs = []
    for path in (cwd, *cwd.parents):
        if path == home:
            break
        dirs.append(path)
        if (path / ".git").exists():
            break
    return dirs


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _enabled_plugins(project_dirs: list[Path], home: Path) -> dict:
    """`enabledPlugins` merged with the CLI's precedence: local > project > user."""
    merged = dict(_read_json(home / ".claude" / "settings.json")
                  .get("enabledPlugins") or {})
    for name in ("settings.json", "settings.local.json"):
        for path in reversed(project_dirs):
            merged.update(_read_json(path / ".claude" / name)
                          .get("enabledPlugins") or {})
    return merged


def _installed_for(entry: dict, project_dirs: list[Path]) -> bool:
    if entry.get("scope") == "user":
        return True
    project = entry.get("projectPath")
    return bool(project) and Path(project).resolve() in project_dirs


def _install_path(entries, project_dirs: list[Path]) -> Optional[Path]:
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if (isinstance(entry, dict) and entry.get("installPath")
                and _installed_for(entry, project_dirs)):
            return Path(entry["installPath"])
    return None


def _plugin_skills(project_dirs: list[Path], home: Path) -> list[str]:
    installed = _read_json(home / ".claude" / "plugins" / "installed_plugins.json")
    enabled = _enabled_plugins(project_dirs, home)
    names = []
    for key, entries in (installed.get("plugins") or {}).items():
        path = _install_path(entries, project_dirs) if enabled.get(key) is True else None
        if path is None:
            continue
        plugin = key.split("@", 1)[0]
        names += [f"{plugin}:{name}" for name in _names_in(path, _PLUGIN_LAYOUT)]
    return names


def available_skills(cwd: str | Path, home: Optional[Path] = None,
                     provider: str = "claude") -> list[dict]:
    """`[{name, source}]` sorted by name; a project skill shadows a user one.

    Raises `ValueError` for an unknown provider id."""
    from lib.providers.registry import build_provider  # noqa: PLC0415
    layout = build_provider(provider).skill_catalog_layout()
    home = (home or Path.home()).resolve()
    root = Path(cwd).expanduser().resolve()
    if not root.is_dir():
        return []
    project_dirs = _project_dirs(root, home)
    found: dict[str, str] = {}
    for path in project_dirs:
        for name in _names_in(path, layout):
            found.setdefault(name, PROJECT)
    for name in _names_in(home, layout):
        found.setdefault(name, USER)
    if layout.plugins:
        for name in _plugin_skills(project_dirs, home):
            found.setdefault(name, PLUGIN)
    return [{"name": name, "source": found[name]} for name in sorted(found)]
