"""Resolve a Bash command's own Claude Code session id.

Claude Code (>= ~2.1) exports the live session id to every child process's
environment as `CLAUDE_CODE_SESSION_ID`. That is authoritative for the current
process — for a *child* session (`CLAUDE_CODE_CHILD_SESSION=1`) it is the
child's own id, which is where that context's trace spans land (unlike the
background-task output directory, which is named with the PARENT session id).
So skills read the id from here (`regin session-id`), never reconstruct it from
a Task tool's output path.

This replaces an earlier cwd-keyed cache (stamped by a `session_id_probe`
PreToolUse hook) that any session running a Bash call in the same directory
could clobber — so it could return a sibling or parent session's id. The env
var has no such failure mode, so the cache and its probe hook were removed.
"""

from __future__ import annotations

import os
from typing import Optional

# Claude Code (>= ~2.1) exports the live session id to every child process's
# environment. This is the authoritative source for the current process.
_ENV_SESSION_ID = "CLAUDE_CODE_SESSION_ID"


def resolve() -> Optional[str]:
    """Return the current Claude Code session id from the environment, or None.

    Reads `CLAUDE_CODE_SESSION_ID`, which Claude Code exports into every Bash
    call's environment. Returns None when it is absent (older Claude Code, or
    invoked outside a session), so callers can treat empty as "omit the flag".
    """
    return os.environ.get(_ENV_SESSION_ID) or None
