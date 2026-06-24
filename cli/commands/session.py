"""`regin session-id` — print the current Claude Code session id.

Claude Code never hands the live session id to Bash, so skills that need it
(`goal preflight --session-id`, `goal feedback --trace-id`, the `gate`
anti-skip checks) used to rely on a hook rewriting a bare `regin session-id`
probe. That broke the moment an agent expanded the probe to the real
interpreter form (`.venv/bin/python cli/regin.py session-id`) — there was no
such subcommand, so it errored.

This makes `session-id` a real subcommand backed by the session cache in
`lib/session_probe.py`: the PreToolUse hook stamps the current session on every
Bash call, and this command resolves the freshest stamp for the cwd (or an
explicit `--nonce`) and prints it. Empty stdout + exit 0 on a miss, so callers
can `SID=$(… session-id)` and simply check for emptiness.
"""

from __future__ import annotations

import os

import typer

from lib.session_probe import resolve


def register(app: typer.Typer) -> None:
    @app.command(
        "session-id",
        help="Print the current Claude Code session id (from the hook-populated cache).",
    )
    def session_id(
        nonce: str = typer.Option(
            None, "--nonce",
            help="Resolve by an explicit correlation token instead of the cwd "
                 "(disambiguates concurrent sessions sharing a directory)."),
    ) -> None:
        sid = resolve(cwd=os.getcwd(), nonce=nonce)
        if sid:
            print(sid)
        # Miss → print nothing, exit 0: the skills treat an empty response as
        # "omit the flag", so this stays a soft, composable failure.
