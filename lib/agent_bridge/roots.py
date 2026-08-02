"""Agent bridge — where a session's autocomplete is rooted.

Both composer surfaces answer *for the target session*, so both ask the same
question: what directory is that session actually running in? `@` paths are
searched under it, and the SDK handshake happens in it (the SDK resolves
project scope from its cwd on its own).

There are two tiers of live session and each keeps its own cwd, so the answer
has to come from both. A user-started terminal registers a `bridge_panes` row
from the SessionStart hook; a run regin launched itself through the Claude
Agent SDK never enters that registry at all and records its cwd in
`agent_runs`. Asking only the pane registry left every regin-spawned session
rootless — and therefore with an empty `/` menu and no `@` suggestions.
`agent_sdk.find_run` is what resolves the SDK tier, because such a run is
traced under two ids (its own `sdk-…` and its child `claude` session's) and
the composer may address it by either.

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


def _sdk_run_cwd(trace_id: str) -> str | None:
    """The cwd of the regin-launched SDK run `trace_id` names, or None.

    Imported inside the call so the tmux tier keeps its current import graph:
    `lib.agent_sdk` is the other capture tier, and a pane's root must not
    become resolvable only when that package imports cleanly.
    """
    from lib.agent_sdk.store import find_run  # noqa: PLC0415

    run = find_run(trace_id)
    return (run or {}).get("cwd") or None


def session_cwd(trace_id: str) -> Path | None:
    """The recorded cwd for `trace_id` in either tier, or None if unknown.

    The pane registry answers first: a tmux session is the only tier that can
    have been *re*-registered on resume, so its row is the fresher fact when a
    trace id somehow exists in both.
    """
    if not trace_id:
        return None
    cwd = store.get_pane_cwd(trace_id) or _sdk_run_cwd(trace_id)
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
