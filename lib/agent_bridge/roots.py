"""Agent bridge — where a session's autocomplete is rooted.

Both composer surfaces answer *for the target session*, so both ask the same
question of the pane registry: what directory is that session actually running
in? `@` paths are searched under it, and the SDK handshake happens in it (the
SDK resolves project scope from its cwd on its own).

`project_root` is the separate, narrower question — the nearest ancestor with a
`.claude/` dir — and exists only to locate the command/skill files that supply
provenance badges. It is never used to scope a search, and it has no fallback:
substituting regin's own root for a session's real cwd is how the accept list
drifts from what that terminal would take, and for provenance it is how rows
that are genuinely `~/.claude/` ones get badged `project`. A session with no
`.claude/` ancestor simply has no project scope.
"""

from __future__ import annotations

from pathlib import Path

from lib.agent_bridge import store


def session_cwd(trace_id: str) -> Path | None:
    """The pane registry's recorded cwd for `trace_id`, or None if unregistered."""
    cwd = store.get_pane_cwd(trace_id)
    return Path(cwd) if cwd else None


def session_dir(trace_id: str) -> Path | None:
    """`session_cwd` narrowed to a cwd that is still a directory on disk.

    A registry row outlives the directory it names (a worktree gets removed, a
    checkout is renamed); answering out of a path that no longer exists is the
    same drift as answering out of regin's root.
    """
    cwd = session_cwd(trace_id)
    return cwd if cwd is not None and cwd.is_dir() else None


def project_root(cwd: Path | None) -> Path | None:
    """Nearest ancestor of `cwd` holding a `.claude/` dir (how Claude Code
    locates project commands), or None when there is none."""
    if cwd is None:
        return None
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".claude").is_dir():
            return candidate
    return None
