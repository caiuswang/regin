"""Typer root for the `regin` CLI.

Defines the top-level app and wires per-domain command modules into
it. Each domain module under `cli/commands/` either exposes a
`register(app)` function (for flat top-level commands) or a pre-built
sub-Typer app (for `regin <group> <cmd>` forms).

This module stays small on purpose — the actual command bodies live
next to the domain they belong to so they can be edited and tested
in isolation.
"""

from __future__ import annotations

import sys
import time

import typer

from lib.activity_log import configure_activity_log, get_activity_logger
from lib.logging_setup import configure_logging
from cli.commands import db, grader, logs, memory, meta, patterns, repo, route, rules, schema, server, skills, topics, trace, users, wiki


app = typer.Typer(
    name="regin",
    help="Pattern reference system for AI Agents",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.callback()
def _root(ctx: typer.Context) -> None:
    """Runs before every command; set up structured logging once and
    record the invocation in the activity log (tagged `feature=cli`)."""
    configure_logging()
    configure_activity_log()
    _stamp_cli_invocation(ctx)


def _stamp_cli_invocation(ctx: typer.Context) -> None:
    """Log command start; arrange a callback to log completion or failure.

    Help/completion invocations (no subcommand) are skipped so they
    don't clutter the activity log with no-op entries."""
    if ctx.invoked_subcommand is None:
        return
    log = get_activity_logger("cli")
    started = time.monotonic()
    log.write("command_invoked", command=ctx.invoked_subcommand, args=sys.argv[1:])

    def _finalize() -> None:
        duration_ms = round((time.monotonic() - started) * 1000, 2)
        exc = sys.exc_info()[1]
        # Click signals clean exits (e.g. --help, typer.Exit(0)) via
        # SystemExit with code 0 — not failures.
        if exc is None or _is_clean_exit(exc):
            log.write("command_completed",
                      command=ctx.invoked_subcommand, duration_ms=duration_ms)
        else:
            log.error("command_failed", exc_info=True,
                      command=ctx.invoked_subcommand,
                      duration_ms=duration_ms,
                      error_type=type(exc).__name__)

    ctx.call_on_close(_finalize)


def _is_clean_exit(exc: BaseException) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "exit_code", None)
    return isinstance(exc, SystemExit) and (code is None or code == 0)


# Flat top-level commands registered from their domain modules.
db.register(app)      # init, rebuild, tags, search
meta.register(app)    # doctor, migrate
repo.register(app)    # discover, sync, status
route.register(app)   # route (experimental dense pattern routing)
server.register(app)  # serve

# Grouped subcommands (`regin users ...` etc.).
app.add_typer(users.users_app)
app.add_typer(skills.skills_app)
app.add_typer(topics.topics_app)
app.add_typer(patterns.pattern_app)
app.add_typer(rules.rules_app)
schema.register(app)  # schema group + flat bootstrap-hook-schemas
app.add_typer(trace.trace_app)
app.add_typer(wiki.wiki_app)
app.add_typer(logs.logs_app)
app.add_typer(memory.memory_app)
app.add_typer(grader.grade_app)


__all__ = ["app"]
