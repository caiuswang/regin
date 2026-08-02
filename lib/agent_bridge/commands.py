"""Agent bridge — the slash-command / skill *accept list* for a session.

Powers the `/live` composer's `/`-triggered autocomplete: the set of slash
commands and skills the *target session* would accept after a leading `/`.

The authoritative source is the Claude Agent SDK: a handshake in the target
session's own cwd returns exactly what the raw terminal offers there —
built-ins, plugin commands, and project/user commands + skills. Falling back to
reading `.claude/` ourselves only approximates that (it can never know the
built-ins), so the filesystem scan is now the *degraded* path: it runs when the
SDK is unavailable, and otherwise only supplies provenance (project / user /
plugin / builtin) for badging, which the SDK payload itself does not carry.

Read-only and fail-closed: a failed handshake, missing dir, unreadable file or
drifted registry degrades to a shorter (or empty) list, never an exception.
The route turns that into `{"commands": []}` so the composer shows no menu.
A session with no registered (or no longer existing) cwd is the empty case,
not regin's own root: this endpoint is editor-reachable by trace id, and
answering an unknown one out of regin's tree both leaks the host's local
catalog and offers commands that session could not run.
"""

from __future__ import annotations

import glob
import os
import re
import threading
import time
from pathlib import Path

import anyio
import yaml

from lib.activity_log import get_activity_logger
from lib.agent_bridge import roots

log = get_activity_logger("agent_bridge")

SDK_CACHE_TTL_SECONDS = 300.0
# A failed handshake is remembered far more briefly than a good list: it must
# not cost every request the full timeout, but a transient auth/spawn failure
# should not pin the session to the degraded list for the whole 5 minutes.
SDK_FAILURE_TTL_SECONDS = 30.0
SDK_TIMEOUT_SECONDS = 20.0

# Built-ins that end or discard the session's state; the composer badges them.
DESTRUCTIVE_COMMANDS = frozenset({"clear", "exit", "logout"})

_META_DESCRIPTION_RE = re.compile(
    r"""description\s*:\s*(['"])(.*?)\1""", re.DOTALL)

_sdk_cache: dict[str, tuple[float, list[dict]]] = {}
_sdk_locks: dict[str, threading.Lock] = {}
_sdk_locks_guard = threading.Lock()


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


def _meta_description(content: str) -> str:
    """The `description` of a workflow's `export const meta = {…}` block.

    A workflow is JS, not frontmatter'd markdown, so there is nothing to parse
    structurally without executing it. Only the single-line quoted form is
    read; anything else degrades to no description, like an unreadable file.
    """
    match = _META_DESCRIPTION_RE.search(content)
    return match.group(2).strip() if match else ""


def _scan_workflows(base: Path, scope: str) -> list[dict]:
    """`<base>/.claude/workflows/*.js` → workflow rows (name = file stem).

    Workflows are offered after a leading `/` exactly like commands, so leaving
    them out of the scan does not hide them — it just badges them `builtin`,
    which is what an SDK name no scan knows falls back to.
    """
    root = base / ".claude" / "workflows"
    rows = []
    for path in sorted(glob.glob(str(root / "*.js"))):
        name = os.path.splitext(os.path.basename(path))[0]
        if name.startswith("_"):
            continue
        rows.append({"name": name,
                     "description": _meta_description(_read_file(path)),
                     "kind": "workflow", "scope": scope})
    return rows


async def _server_info_commands(root: str) -> list[dict]:
    """The SDK's command list for `root` — the raw terminal's own catalog."""
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    async with ClaudeSDKClient(options=ClaudeAgentOptions(cwd=root)) as client:
        info = await client.get_server_info()
    entries = (info or {}).get("commands") or []
    return [dict(entry) for entry in entries
            if isinstance(entry, dict) and entry.get("name")]


async def _sdk_handshake(root: str) -> list[dict]:
    with anyio.fail_after(SDK_TIMEOUT_SECONDS):
        return await _server_info_commands(root)


def _fetch_sdk_commands(root: str) -> list[dict]:
    """Sync bridge to the async handshake; [] on ANY failure (never raises).

    Import error, missing CLI, auth failure, a child that never answers — all
    collapse to the empty list so the caller falls back to the scan. The route
    is sync Flask, hence `anyio.run` rather than an awaited call.
    """
    try:
        return anyio.run(_sdk_handshake, root)
    except Exception:  # noqa: BLE001 — degrade to the filesystem scan
        log.error("bridge_sdk_commands_failed", exc_info=True)
        return []


def _cached_sdk_commands(root: str) -> list[dict] | None:
    cached = _sdk_cache.get(root)
    return cached[1] if cached and cached[0] > time.monotonic() else None


def _root_lock(root: str) -> threading.Lock:
    with _sdk_locks_guard:
        return _sdk_locks.setdefault(root, threading.Lock())


def _sdk_commands(root: str) -> list[dict]:
    """`_fetch_sdk_commands` behind a per-root TTL cache (the handshake is ~3s).

    The lock is per root and held across the handshake so a burst of cold
    requests for one session spawns one `claude` child, not N; a *different*
    root meanwhile is never blocked behind it.
    """
    hit = _cached_sdk_commands(root)
    if hit is not None:
        return hit
    with _root_lock(root):
        hit = _cached_sdk_commands(root)
        if hit is not None:
            return hit
        rows = _fetch_sdk_commands(root)
        ttl = SDK_CACHE_TTL_SECONDS if rows else SDK_FAILURE_TTL_SECONDS
        _sdk_cache[root] = (time.monotonic() + ttl, rows)
        return rows


def _scan_scope(base: Path, scope: str) -> list[dict]:
    return (_scan_commands(base, scope) + _scan_skills(base, scope)
            + _scan_workflows(base, scope))


def _scan_rows(project: Path | None, home: Path) -> list[dict]:
    """Filesystem-scanned rows, project scope first (so it shadows user).

    `project` is None for a session with no `.claude/` ancestor: it then has no
    project scope at all, and every row it can see is a user one.
    """
    project_rows = _scan_scope(project, "project") if project else []
    return project_rows + _scan_scope(home, "user")


def _provenance(scanned: list[dict]) -> dict[str, tuple[str, str]]:
    """name → (kind, scope) from the scan; project wins over user."""
    index: dict[str, tuple[str, str]] = {}
    for row in scanned:
        index.setdefault(row["name"], (row["kind"], row["scope"]))
    return index


def _classify(name: str, index: dict[str, tuple[str, str]]) -> tuple[str, str]:
    """Kind + scope for an SDK command, which carries neither.

    The scan is asked first and wins: a project command in a nested directory
    is namespaced `parent:child` too, so treating every colon as Claude's
    plugin namespacing mislabels real project commands. The heuristic only
    decides names neither scan knows.
    """
    known = index.get(name)
    if known:
        return known
    return ("plugin", "plugin") if ":" in name else ("builtin", "builtin")


def _row(name: str, description: str, argument_hint: str,
         kind: str, scope: str, aliases: list[str] | None = None) -> dict:
    destructive = kind == "builtin" and name in DESTRUCTIVE_COMMANDS
    return {"name": name, "description": description,
            "argumentHint": argument_hint, "kind": kind, "scope": scope,
            "aliases": aliases or [],
            "risk": "destructive" if destructive else None}


def _aliases(entry: dict) -> list[str]:
    """The SDK's alternate names for a command (`/new` for `/clear`).

    The terminal accepts them, so the menu has to be able to match them; the
    canonical `name` is still what gets inserted.
    """
    raw = entry.get("aliases")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(alias) for alias in raw if isinstance(alias, str) and alias]


def _sdk_row(entry: dict, index: dict[str, tuple[str, str]]) -> dict:
    name = str(entry.get("name") or "")
    kind, scope = _classify(name, index)
    return _row(name, str(entry.get("description") or ""),
                str(entry.get("argumentHint") or ""), kind, scope,
                _aliases(entry))


def _matches_scope(entry: dict, provenance: tuple[str, str] | None) -> bool:
    """Does this SDK row describe the scope the badge will claim?

    The SDK reports a name shadowed across scopes twice, distinguished only by
    a `(project)` / `(user)` suffix on the description.
    """
    if not provenance:
        return False
    description = str(entry.get("description") or "").rstrip()
    return description.endswith(f"({provenance[1]})")


def _pick_variants(entries: list[dict],
                   index: dict[str, tuple[str, str]]) -> list[dict]:
    """One entry per name, preferring the variant whose description matches
    the scope the scan resolves — the terminal runs the project one, so
    keeping the SDK's first (user) row would contradict the badge."""
    chosen: dict[str, dict] = {}
    for entry in entries:
        name = str(entry.get("name") or "")
        if name not in chosen or _matches_scope(entry, index.get(name)):
            chosen[name] = entry
    return list(chosen.values())


def _scanned_row(entry: dict) -> dict:
    return _row(entry["name"], entry.get("description", ""), "",
                entry["kind"], entry["scope"])


def _ordered(rows: list[dict]) -> list[dict]:
    """Dedup by name (first writer wins) and sort: built-ins last, then kind, name."""
    seen: dict[str, dict] = {}
    for row in rows:
        seen.setdefault(row["name"], row)
    return sorted(seen.values(),
                  key=lambda r: (r["scope"] == "builtin", r["kind"], r["name"]))


def list_session_commands(trace_id: str) -> list[dict]:
    """The dedup'd, sorted accept list for a session.

    Prefers the SDK handshake in the session's own cwd — that list is what the
    raw terminal would offer *there*, which is the whole point; handshaking
    somewhere else (regin's root) offers commands the session cannot run. The
    `.claude/` scan is cross-referenced only to label each row's kind/scope,
    and for that the nearest `.claude/` ancestor is the right base. With no SDK
    answer the scan itself becomes the list (project entries shadowing
    same-named user ones), which loses built-ins and plugins but never raises.

    A session with no usable cwd gets an EMPTY catalog, exactly like
    `list_session_files`: there is no directory to enumerate for, and standing
    regin's own root in its place would answer an arbitrary trace id with this
    host's local command and skill names.
    """
    cwd = roots.session_dir(trace_id)
    if cwd is None:
        log.read("bridge_commands_listed", trace_id=trace_id, count=0,
                 source="none")
        return []
    scanned = _scan_rows(roots.project_root(cwd), Path.home())
    sdk = _sdk_commands(str(cwd))
    index = _provenance(scanned)
    rows = ([_sdk_row(entry, index) for entry in _pick_variants(sdk, index)]
            if sdk else [_scanned_row(entry) for entry in scanned])
    ordered = _ordered(rows)
    log.read("bridge_commands_listed", trace_id=trace_id, count=len(ordered),
             source="sdk" if sdk else "scan")
    return ordered
