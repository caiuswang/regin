"""Agent bridge — the slash-command / skill *accept list* for a session.

Powers the `/live` composer's `/`-triggered autocomplete: the set of slash
commands and skills the *target session* would accept after a leading `/`.
We enumerate the very directories Claude Code itself reads to resolve them —
project `.claude/` (scoped to the session's own project via the pane
registry's recorded `cwd`) plus the user's `~/.claude/` — so the list matches
what that session actually accepts, not a regin-local approximation.

Read-only and fail-closed: any missing dir / unreadable file / drifted
registry degrades to a shorter (or empty) list, never an exception. The route
turns that into `{"commands": []}` so the composer simply shows no menu.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

import yaml

from lib.activity_log import get_activity_logger
from lib.agent_bridge import store
from lib.settings import settings

log = get_activity_logger("agent_bridge")


def _read_file(filepath: str) -> str:
    """File text, or '' on any read error (fail-closed)."""
    try:
        with open(filepath, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _frontmatter_description(content: str) -> str:
    """The YAML-frontmatter `description`, or '' — mirrors db_rebuild's split."""
    if not content.startswith("---"):
        return ""
    parts = content.split("---", 2)
    if len(parts) < 3:
        return ""
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return ""
    desc = meta.get("description") if isinstance(meta, dict) else None
    return str(desc).strip() if desc else ""


def _first_prose_line(content: str) -> str:
    """First non-heading, non-fence prose line of the body (past frontmatter).

    Slash-command files (`.claude/commands/*.md`) carry no frontmatter but an
    `# H1` then a summary sentence — that sentence is the useful description.
    """
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "```")):
            return stripped
    return ""


def _read_description(filepath: str) -> str:
    """Best description for a command/skill file: frontmatter, else first prose.

    Swallows every read/parse error into '' — a malformed file just shows in
    the menu without a description rather than breaking the whole list.
    """
    content = _read_file(filepath)
    if not content:
        return ""
    return _frontmatter_description(content) or _first_prose_line(content)


def _resolve_project_root(trace_id: str) -> Path:
    """The target session's project root, falling back to regin's own.

    Walks up from the pane registry's recorded `cwd` to the nearest ancestor
    holding a `.claude/` dir (how Claude Code locates project commands). No
    registered cwd, or none on the path, → `settings.project_root`.
    """
    cwd = store.get_pane_cwd(trace_id)
    if cwd:
        here = Path(cwd)
        for candidate in (here, *here.parents):
            if (candidate / ".claude").is_dir():
                return candidate
    return Path(settings.project_root)


def _scan_commands(base: Path, scope: str) -> list[dict]:
    """`<base>/.claude/commands/**/*.md` → command rows.

    Name is the path under `commands/` without `.md`, nested dirs joined by
    `:` (Claude's `parent:child` slash form). Skips `_`-prefixed files.
    """
    root = base / ".claude" / "commands"
    rows = []
    for path in sorted(glob.glob(str(root / "**" / "*.md"), recursive=True)):
        rel = os.path.relpath(path, root)
        name = os.path.splitext(rel)[0].replace(os.sep, ":")
        if os.path.basename(rel).startswith("_"):
            continue
        rows.append({"name": name, "description": _read_description(path),
                     "kind": "command", "scope": scope})
    return rows


def _scan_skills(base: Path, scope: str) -> list[dict]:
    """`<base>/.claude/skills/*/SKILL.md` → skill rows (name = skill dir)."""
    root = base / ".claude" / "skills"
    rows = []
    for path in sorted(glob.glob(str(root / "*" / "SKILL.md"))):
        name = os.path.basename(os.path.dirname(path))
        if name.startswith("_"):
            continue
        rows.append({"name": name, "description": _read_description(path),
                     "kind": "skill", "scope": scope})
    return rows


def list_session_commands(trace_id: str) -> list[dict]:
    """The dedup'd, sorted accept list for a session.

    Union of project (`cwd`-scoped) and user (`~/.claude`) commands + skills.
    A project entry shadows a user entry of the same name. Sorted by kind
    (command before skill) then name. Never raises.
    """
    project = _resolve_project_root(trace_id)
    home = Path.home()
    rows = (_scan_commands(project, "project") + _scan_skills(project, "project")
            + _scan_commands(home, "user") + _scan_skills(home, "user"))
    seen: dict[str, dict] = {}
    for row in rows:
        # First writer wins: project sources precede user sources above, so a
        # project entry shadows a same-named user one.
        seen.setdefault(row["name"], row)
    ordered = sorted(seen.values(),
                     key=lambda r: (0 if r["kind"] == "command" else 1, r["name"]))
    log.read("bridge_commands_listed", trace_id=trace_id, count=len(ordered))
    return ordered
