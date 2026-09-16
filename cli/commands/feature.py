"""`regin feature` — where a feature stands, and what it cost.

Two commands, deliberately not one. **`stage` stays a glance**: the pipeline
skill runs it first, `regin gate pipeline-ran` counts its span, and it cannot
answer at all without the tracker — so it exits 2 there, because the tracker
*is* the answer. It prints the tracker's own sentence rather than a fixed one:
"unreachable" and "no such issue" are different facts, and on a local provider
only the second can happen. **`ledger` is the report**: mostly local, useful with the
tracker down, and therefore exits 0 and prints `unknown` for the one field it
could not fetch. One exit-code policy cannot serve both questions.

**`attribute` is the only writer of an attribution.** Nothing infers one —
not a cwd, not a branch name (regin captures no branch anywhere), not a clock.
See `docs/feature-pipeline.md`.
"""

from __future__ import annotations

import json
from typing import Optional

import typer

from lib.defects import stage as stage_module
from lib.defects import tracker
from lib.features import attribution, spans, ledger as ledger_module

feature_app = typer.Typer(
    name="feature",
    help="Where a feature stands in the pipeline, and what it cost",
    no_args_is_help=True,
)


@feature_app.command("stage")
def stage_cmd(
    key: str = typer.Argument(..., help="Feature key, e.g. CAI-215"),
    session: Optional[str] = typer.Option(
        None, "--session", help="This session's trace id — leaves the span "
                                "`regin gate pipeline-ran` counts"),
) -> None:
    """Print the feature's stage, rounds, open defects and escapes.

    Consulting the pipeline is not declaring a feature: this fires in sessions
    that then go and do something else, so reading it as an attribution would
    be inference-by-proxy. `regin feature attribute` is the declaration.
    """
    try:
        result = stage_module.stage_for_key(key)
    except tracker.TrackerUnavailable as exc:
        typer.echo(f"unknown — {exc}", err=True)
        raise typer.Exit(2)
    typer.echo(f"{result.key}  {result.name}  rounds={result.rounds}  "
               f"open_defects={result.open_defects}  "
               f"escapes={result.escapes}")
    typer.echo(f"  {result.blurb}")
    stage_module.fingerprint(session, result)


@feature_app.command("attribute")
def attribute_cmd(
    key: str = typer.Argument(..., help="Feature key, e.g. CAI-242"),
    session: str = typer.Option(
        ..., "--session", help="The trace being attributed. Attributing a "
                               "trace other than your own is recorded as "
                               "`recalled` — counted and labelled, never "
                               "filtered."),
    primary: bool = typer.Option(
        False, "--primary", help="Make this the session's primary feature, "
                                 "demoting the incumbent. Primary is "
                                 "first-writer-wins without this."),
) -> None:
    """Declare that a session was building this feature.

    Runnable long after the session ended — that is the normal case, and
    nothing about the act is time-bounded.
    """
    from lib.session_probe import resolve  # noqa: PLC0415
    row = attribution.attribute(key, session, primary=primary,
                                attributing_trace=resolve())
    typer.echo(f"{row.feature_key}  {row.trace_id}  {row.provenance}"
               f"{'  primary' if row.is_primary else ''}"
               f"  round={row.round if row.round is not None else '—'}")
    _fingerprint(row)


def _fingerprint(row) -> None:
    """Leave the `feature.attributed` span — the declaration in the trace.

    The ledger leaves none: a second span would let an idle report satisfy
    `regin gate pipeline-ran`, which exists to prove the *stage* was consulted.
    """
    spans.attributed(row)


@feature_app.command("unattribute")
def unattribute_cmd(
    key: str = typer.Argument(..., help="Feature key, e.g. CAI-242"),
    session: str = typer.Option(..., "--session",
                                help="Trace id of the session to withdraw"),
) -> None:
    """Withdraw a declaration.

    The span it leaves does not erase the one the declaration left — spans are
    append-only, so the pair reads as a history rather than as a correction
    nobody can audit.

    A session reaching the feature through a task link has nothing to withdraw
    here: the assertion is the link on the task.
    """
    if not attribution.unattribute(key, session):
        typer.echo(f"{session} declared no {key} — nothing withdrawn. A task "
                   "link is withdrawn on the task, not here.", err=True)
        raise typer.Exit(1)
    spans.unattributed(key, attribution.canonical_trace(session))
    typer.echo(f"{key}  {session}  unattributed")


@feature_app.command("ledger")
def ledger_cmd(
    key: str = typer.Argument(..., help="Feature key, e.g. CAI-242"),
    as_json: bool = typer.Option(False, "--json",
                                 help="Emit the nested JSON form"),
) -> None:
    """The sessions that built a feature, what they cost, and what escaped.

    Cost is an upper bound, permanently: attribution binds a whole session and
    the data holds no sub-session boundary. Every figure prints its own gaps,
    and a gap is a count of sessions — never a substituted zero.
    """
    report = ledger_module.build(key)
    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2))
        return
    for line in ledger_module.render(report):
        typer.echo(line)
