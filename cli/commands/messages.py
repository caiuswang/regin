"""`regin messages ...` — inspect and prune the send_to_user inbox.

The `agent_messages` table (the `/inbox` feed) is otherwise grow-forever:
keyless `send_to_user` calls always insert, and dismissing only soft-flags
a row. These commands give a manual cleanup path; `settings.agent_messages.
retention_days` enforces the same prune automatically after each write.

Subcommands:
  stats  — counts (total / unread / dismissed / pinned / test) + oldest row
  prune  — hard-delete rows by age / dismissed / keep-newest-N criteria
"""

from __future__ import annotations

import json as _json
from typing import Optional

import typer

from lib.agent_messages import store


messages_app = typer.Typer(
    name="messages",
    help="Inspect and prune the send_to_user inbox (agent_messages)",
    no_args_is_help=True,
)


@messages_app.command("stats", help="Show inbox counts and the oldest row.")
def cmd_stats(
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    s = store.message_stats()
    if json:
        print(_json.dumps(s))
        return
    print(f"total:     {s['total']}")
    print(f"unread:    {s['unread']}")
    print(f"dismissed: {s['dismissed']}")
    print(f"pinned:    {s['pinned']}")
    print(f"tests:     {s['tests']}")
    print(f"oldest:    {s['oldest'] or '—'}")


@messages_app.command(
    "prune",
    help="Hard-delete inbox messages. Requires at least one of "
         "--older-than / --dismissed-only / --keep.",
)
def cmd_prune(
    older_than: Optional[int] = typer.Option(
        None, "--older-than", help="Delete rows older than N days."),
    dismissed_only: bool = typer.Option(
        False, "--dismissed-only", help="Only delete already-dismissed rows."),
    keep: Optional[int] = typer.Option(
        None, "--keep", help="Keep the N newest matching rows, delete the rest."),
    keep_pinned: bool = typer.Option(
        True, "--keep-pinned/--no-keep-pinned",
        help="Protect pinned rows from deletion (default: on)."),
    include_tests: bool = typer.Option(
        True, "--include-tests/--no-include-tests",
        help="Include is_test rows in the prune (default: on)."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report the count without deleting."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    if older_than is None and not dismissed_only and keep is None:
        print("error: specify at least one of "
              "--older-than N | --dismissed-only | --keep M")
        raise typer.Exit(2)

    kwargs = dict(older_than_days=older_than, dismissed_only=dismissed_only,
                  keep=keep, keep_pinned=keep_pinned, include_tests=include_tests)
    would = store.prune_messages(**kwargs, dry_run=True)

    if dry_run:
        print(f"would delete {would} message(s)")
        return
    if would == 0:
        print("nothing to prune")
        return
    if not yes:
        typer.confirm(f"Delete {would} message(s)?", abort=True)

    deleted = store.prune_messages(**kwargs)
    print(f"deleted {deleted} message(s)")


def register(app: typer.Typer) -> None:
    app.add_typer(messages_app)
