"""`regin usage measure` — rate-limit alerts from a session's measurement.

The regin-meter mod pipes Claude Code's `session.measure` input here (JSON on
stdin) whenever a rate-limit window moved. Each window reaching a threshold in
`settings.agent_messages.rate_limit_alert_at` raises one `warning` message,
once per window and threshold across all sessions (`lib.agent_messages.
rate_limits`).
"""

from __future__ import annotations

import sys

import typer
from pydantic import ValidationError

from lib.agent_messages.rate_limits import Measure, alert_crossings


usage_app = typer.Typer(
    name="usage",
    help="Act on a session's usage measurements (rate-limit alerts)",
    no_args_is_help=True,
)


@usage_app.command("measure",
                   help="Read one session.measure input on stdin and raise "
                        "any rate-limit alert it crosses.")
def cmd_measure(
    session: str = typer.Option(..., "--session",
                                help="The session the measurement is from."),
) -> None:
    try:
        measure = Measure.model_validate_json(sys.stdin.read() or "{}")
    except ValidationError as exc:
        print(f"invalid measurement: {exc}", file=sys.stderr)
        raise typer.Exit(2)
    for message in alert_crossings(session, measure):
        print(message["title"])
