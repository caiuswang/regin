"""`regin session-id` — print the current Claude Code session id.

Claude Code (>= ~2.1) exports the live id to every Bash call as the
`CLAUDE_CODE_SESSION_ID` env var; this command is a thin wrapper that prints
it. Skills that need the id (`goal feedback --trace-id`, the `gate` anti-skip
checks) call `SID=$(… session-id)` and treat empty stdout as "omit the flag".

Empty stdout + exit 0 on a miss (older Claude Code that doesn't export the
var), so callers can `SID=$(… session-id)` and simply check for emptiness.
"""

from __future__ import annotations

import typer

from lib.session_probe import resolve


def register(app: typer.Typer) -> None:
    @app.command(
        "session-id",
        help="Print the current Claude Code session id (from $CLAUDE_CODE_SESSION_ID).",
    )
    def session_id() -> None:
        sid = resolve()
        if sid:
            print(sid)
        # Miss → print nothing, exit 0: skills treat an empty response as
        # "omit the flag", so this stays a soft, composable failure.
