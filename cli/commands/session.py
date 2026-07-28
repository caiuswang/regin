"""`regin session-id` — print the current agent session's id.

Resolution is provider-agnostic (see `lib/session_probe.py`): the
harness-agnostic `REGIN_SESSION_ID` override first, then whatever variable the
running CLI exports (`CLAUDE_CODE_SESSION_ID` for Claude Code). Skills that
need the id (`goal feedback --trace-id`, the `gate` anti-skip checks) call
`SID=$(… session-id)` and treat empty stdout as "omit the flag".

Empty stdout + exit 0 on a miss, so callers can `SID=$(… session-id)` and
simply check for emptiness. `--from-trace` adds a second chance for harnesses
that export nothing: the one live session regin's hooks recorded for this
directory, or nothing at all when that is ambiguous.
"""

from __future__ import annotations

import typer

from lib.session_probe import resolve, resolve_from_trace


def register(app: typer.Typer) -> None:
    @app.command(
        "session-id",
        help="Print the current agent session id (from $REGIN_SESSION_ID or "
             "the provider's own variable, e.g. $CLAUDE_CODE_SESSION_ID).",
    )
    def session_id(
        from_trace: bool = typer.Option(
            False, "--from-trace", "-t",
            help="On an env miss, fall back to the single live session regin "
                 "recorded for this directory. For harnesses that export no "
                 "session id; prints nothing when two sessions are live here."),
    ) -> None:
        sid = resolve()
        if not sid and from_trace:
            sid = resolve_from_trace()
        if sid:
            print(sid)
        # Miss → print nothing, exit 0: skills treat an empty response as
        # "omit the flag", so this stays a soft, composable failure.
