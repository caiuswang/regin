"""`regin events ...` — inspect the declared notification event bus.

Every notifiable system event (proposal ready, content drift, grade
finished, the permission/plan blockers, …) is declared once in
`lib.agent_messages.events.REGISTRY` and published through a single `emit`.
`list` enumerates that registry with each kind's severity and current
enablement, so "what can reach my inbox" has one source of truth.
"""

from __future__ import annotations

import json as _json

import typer

from lib.agent_messages import events

events_app = typer.Typer(
    name="events",
    help="Inspect the declared notification event bus (agent_messages).",
    no_args_is_help=True,
)


@events_app.callback()
def _main() -> None:
    """Inspect the declared notification event bus (agent_messages).

    A callback keeps `events` a command *group* even with a single
    subcommand, so `regin events list` (not a collapsed root command) works.
    """


@events_app.command("list", help="List every declared notifiable event kind.")
def cmd_list(
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    rows = events.catalog()
    if json:
        print(_json.dumps(rows))
        return
    print(f"{'KIND':<20} {'SEVERITY':<9} {'ENABLED':<8} SUMMARY")
    for r in rows:
        enabled = "yes" if r["enabled"] else "no"
        print(f"{r['kind']:<20} {r['severity']:<9} {enabled:<8} {r['summary']}")
