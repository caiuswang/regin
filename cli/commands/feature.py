"""`regin feature` — where a feature stands, and what it cost.

**`stage` stays a glance**: the pipeline skill runs it first and
`regin gate pipeline-ran` counts its span. It used to exit 2 when no tracker
could answer, because the tracker *was* the answer; the stage is derived from
rows in this database now, so there is no unreachable reading left and the
only exit-2 is a feature id that names nothing. **`ledger` is the report**.

Every command takes a feature **id**. `--ref` is the same argument spelled as a
remote tracker's identifier, for the case where that is what an operator has in
front of them.

**`attribute` is the only writer of an attribution.** Nothing infers one —
not a cwd, not a branch name (regin captures no branch anywhere), not a clock.
See *Feature pipeline* in `ARCHITECTURE.md`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from lib.features import attribution, events, spans, ledger as ledger_module
from lib.features import store as feature_store
from lib.features import sync as sync_module

feature_app = typer.Typer(
    name="feature",
    help="Where a feature stands in the pipeline, and what it cost",
    no_args_is_help=True,
)


def _resolve(key: str, ref: Optional[str]) -> int:
    """The feature id an operator named, by id or by remote reference."""
    if ref is not None:
        from lib.tasks.integrations import IntegrationError, registry  # noqa: PLC0415
        # `name_ref`, not `resolve_ref`: this resolves to a LOCAL row, so
        # demanding a live credential would refuse a lookup needing no network.
        try:
            parsed = registry.name_ref(ref)
        except IntegrationError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2)
        row = feature_store.by_external(parsed.provider, parsed.key)
        if row is None:
            typer.echo(f"no feature carries {parsed.key}", err=True)
            raise typer.Exit(2)
        return row.id
    try:
        feature_id = int(key)
    except (TypeError, ValueError):
        typer.echo(f"{key!r} is not a feature id — `--ref {key}` if that is a "
                   "tracker reference", err=True)
        raise typer.Exit(2)
    if feature_store.get(feature_id) is None:
        typer.echo(f"no feature {feature_id}", err=True)
        raise typer.Exit(2)
    return feature_id


@feature_app.command("stage")
def stage_cmd(
    key: str = typer.Argument(..., help="Feature id, e.g. 42"),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference "
                            "(e.g. CAI-274) instead of its id"),
    session: Optional[str] = typer.Option(
        None, "--session", help="This session's trace id — leaves the span "
                                "`regin gate pipeline-ran` counts"),
) -> None:
    """Print the feature's stage, rounds, open defects and escapes.

    Consulting the pipeline is not declaring a feature: this fires in sessions
    that then go and do something else, so reading it as an attribution would
    be inference-by-proxy. `regin feature attribute` is the declaration.
    """
    feature_id = _resolve(key, ref)
    report = ledger_module.build(feature_id)
    typer.echo(f"#{feature_id}  {report.stage}  rounds={report.rounds}  "
               f"open_defects={report.open_defects}  "
               f"escapes={report.escapes.escapes}")
    typer.echo(f"  {events.blurb(report.stage)}")
    spans.stage_consulted(session, {
        "feature_id": feature_id, "stage": report.stage,
        "blurb": events.blurb(report.stage), "rounds": report.rounds,
        "open_defects": report.open_defects,
        "escapes": report.escapes.escapes})


@feature_app.command("attribute")
def attribute_cmd(
    key: str = typer.Argument(..., help="Feature id, e.g. 42"),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference"),
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
    row = attribution.attribute(_resolve(key, ref), session, primary=primary,
                                attributing_trace=resolve())
    typer.echo(f"#{row.feature_id}  {row.trace_id}  {row.provenance}"
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
    key: str = typer.Argument(..., help="Feature id, e.g. 42"),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference"),
    session: str = typer.Option(..., "--session",
                                help="Trace id of the session to withdraw"),
) -> None:
    """Withdraw a declaration.

    The span it leaves does not erase the one the declaration left — spans are
    append-only, so the pair reads as a history rather than as a correction
    nobody can audit.

    A session reaching the feature through a run has nothing to withdraw here:
    the assertion is the task's placement under the feature.
    """
    feature_id = _resolve(key, ref)
    if not attribution.unattribute(feature_id, session):
        typer.echo(f"{session} declared no feature {feature_id} — nothing "
                   "withdrawn. A run's attribution is withdrawn by moving the "
                   "task, not here.", err=True)
        raise typer.Exit(1)
    spans.unattributed(feature_id, attribution.canonical_trace(session))
    typer.echo(f"#{feature_id}  {session}  unattributed")


@feature_app.command("ledger")
def ledger_cmd(
    key: str = typer.Argument(..., help="Feature id, e.g. 42"),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference"),
    as_json: bool = typer.Option(False, "--json",
                                 help="Emit the nested JSON form"),
) -> None:
    """The sessions that built a feature, what they cost, and what escaped.

    Cost is an upper bound, permanently: attribution binds a whole session and
    the data holds no sub-session boundary. Every figure prints its own gaps,
    and a gap is a count of sessions — never a substituted zero.
    """
    report = ledger_module.build(_resolve(key, ref))
    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2))
        return
    for line in ledger_module.render(report):
        typer.echo(line)


@feature_app.command("new")
def new_cmd(
    title: str = typer.Argument(..., help="What is being delivered"),
    body: Optional[str] = typer.Option(
        None, "--spec", help="The spec itself. Thin prose; no file paths — at "
                             "spec time you cannot know which files this will "
                             "touch, and naming them specifies the "
                             "implementation before the design exists."),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Adopt an existing tracker issue instead. The "
                            "title comes from the fetch, not from you."),
    session: Optional[str] = typer.Option(
        None, "--session", help="This session's trace id — recorded on the "
                                "`specced` event as the run that minted it"),
    project: Optional[str] = typer.Option(
        None, "--project", help="Registered repo name to file this under "
                                "(see `regin repos`)"),
    follows: Optional[int] = typer.Option(
        None, "--follows", help="Id of the feature this is the next phase of "
                                "— linked in the same transaction"),
) -> None:
    """Declare a feature. The row and its `specced` event, in one transaction.

    `--session` records who minted the feature, not who builds it: it writes
    no attribution, because `attribute` is the only writer of one.
    """
    from cli.commands.tasks import _project_id  # noqa: PLC0415
    project_id = _project_id(project)
    session = session or None
    if ref is not None:
        from lib.tasks.integrations import IntegrationError  # noqa: PLC0415
        try:
            row = sync_module.adopt(ref, project_id=project_id,
                                    trace_id=session)
        except IntegrationError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2)
    else:
        try:
            row = feature_store.create(title, body=body, trace_id=session,
                                       project_id=project_id, follows=follows)
        except feature_store.FeatureError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2)
    typer.echo(f"#{row.id}  {row.title}  {events.stage(row.id)}")


def _link_args(key: str, other: str, kind: str) -> tuple[int, int, str]:
    from lib.orm.models.features import LINK_KINDS  # noqa: PLC0415
    if kind not in LINK_KINDS:
        typer.echo(f"--kind must be one of {', '.join(LINK_KINDS)}", err=True)
        raise typer.Exit(2)
    return _resolve(key, None), _resolve(other, None), kind


@feature_app.command("link")
def link_cmd(
    key: str = typer.Argument(..., help="Feature id"),
    other: str = typer.Argument(..., help="The feature to link it to"),
    kind: str = typer.Option(..., "--kind", help="follows (KEY is the next "
                                                 "phase of OTHER) or related"),
) -> None:
    """Link two features as phases of one line of work, or as related."""
    from lib.features import links  # noqa: PLC0415
    a, b, kind = _link_args(key, other, kind)
    try:
        links.link(a, b, kind)
    except links.LinkRefused as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"#{a}  {kind}  #{b}")


@feature_app.command("unlink")
def unlink_cmd(
    key: str = typer.Argument(..., help="Feature id"),
    other: str = typer.Argument(..., help="The linked feature"),
    kind: str = typer.Option(..., "--kind", help="follows or related"),
) -> None:
    """Remove one link. A link that was never there is not an error."""
    from lib.features import links  # noqa: PLC0415
    a, b, kind = _link_args(key, other, kind)
    removed = links.unlink(a, b, kind)
    typer.echo(f"#{a}  {kind}  #{b}  {'unlinked' if removed else 'not linked'}")


def _spec_source(body: Optional[str], path: Optional[str]) -> str:
    if path is not None:
        try:
            body = (sys.stdin.read() if path == "-"
                    else Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            typer.echo(f"cannot read {path}: {exc}", err=True)
            raise typer.Exit(2)
    # `--file -` run with nothing piped in reads EOF; written through, that
    # wipes the spec with no hint anything went wrong. Emptying is `--clear`'s.
    if not (body or "").strip():
        typer.echo("the spec given is empty — nothing written; "
                   "`--clear` is how to empty a spec", err=True)
        raise typer.Exit(2)
    return body


@feature_app.command("spec")
def spec_cmd(
    key: str = typer.Argument(..., help="Feature id"),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference"),
    body: Optional[str] = typer.Option(
        None, "--spec", help="The spec itself. Thin prose"),
    path: Optional[str] = typer.Option(
        None, "--file", help="Read the spec from a file; `-` reads stdin"),
    clear: bool = typer.Option(
        False, "--clear", help="Empty the spec"),
) -> None:
    """Read or replace the spec, which lives on the row and nowhere else.

    A spec written into the repo is the copy nothing re-reads: it is not what
    a run's prompt carries and no surface serves it, so it states the design
    as it was the day someone typed it. `--file` is why this command exists —
    a real spec is thousands of characters with newlines, and argv is the
    wrong pipe for one.
    """
    feature_id = _resolve(key, ref)
    sources = [body is not None, path is not None, clear]
    if sum(sources) > 1:
        typer.echo("name one source for the spec: --spec, --file or --clear",
                   err=True)
        raise typer.Exit(2)
    if not any(sources):
        typer.echo(feature_store.require(feature_id).body
                   or "(no spec — `--spec`, or `--file` for a long one)")
        return
    try:
        row = feature_store.set_body(
            feature_id, None if clear else _spec_source(body, path))
    except feature_store.FeatureError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    typer.echo(f"#{row.id}  spec {len(row.body or '')} characters")


@feature_app.command("image")
def image_cmd(
    key: str = typer.Argument(..., help="Feature id"),
    idx: int = typer.Argument(..., help="The N of the spec's `![](image:N)`"),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference"),
    out: Optional[str] = typer.Option(
        None, "--out", help="Where to write it (default: a temp file)"),
) -> None:
    """Write one of the feature's images to a file and print its path.

    A run's launch carries only its task's own images; this is how a run whose
    spec cites a mockup reads that one picture and no other.
    """
    from lib.features import images as feature_images  # noqa: PLC0415

    feature_id = _resolve(key, ref)
    found = feature_images.get_bytes(feature_id, idx)
    if found is None:
        held = [row["idx"] for row in feature_images.list_for(feature_id)]
        typer.echo(f"feature #{feature_id} has no image {idx} "
                   f"(it has: {', '.join(map(str, held)) or 'none'})", err=True)
        raise typer.Exit(2)
    from lib.attached_images import write_file  # noqa: PLC0415

    data, media_type = found
    typer.echo(str(write_file("feature", feature_id, idx, data, media_type,
                              out)))


@feature_app.command("ship")
def ship_cmd(
    key: str = typer.Argument(..., help="Feature id"),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference"),
    session: Optional[str] = typer.Option(
        None, "--session", help="This session's trace id"),
) -> None:
    """The done-claim. This is the boundary later defects are blamed against.

    Nothing else in regin writes it: a run settling says *this session
    finished*, never *this shipped*, and the two sharing a column is what let a
    settling run ship a feature nobody claimed.
    """
    feature_id = _resolve(key, ref)
    _declare(feature_store.ship, feature_id, session)
    typer.echo(f"#{feature_id}  {events.stage(feature_id)}  "
               f"rounds={events.rounds(feature_id)}")


def _declare(verb, feature_id: int, session: Optional[str]) -> None:
    try:
        verb(feature_id, trace_id=session)
    except feature_store.FeatureError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)


@feature_app.command("reopen")
def reopen_cmd(
    key: str = typer.Argument(..., help="Feature id"),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference"),
    session: Optional[str] = typer.Option(
        None, "--session", help="This session's trace id"),
) -> None:
    """Walk a shipped or escaped feature back (+1 round), or return an
    abandoned one to spec (no round)."""
    feature_id = _resolve(key, ref)
    _declare(feature_store.reopen, feature_id, session)
    typer.echo(f"#{feature_id}  {events.stage(feature_id)}  "
               f"rounds={events.rounds(feature_id)}")


@feature_app.command("abandon")
def abandon_cmd(
    key: str = typer.Argument(..., help="Feature id"),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference"),
    session: Optional[str] = typer.Option(
        None, "--session", help="This session's trace id"),
) -> None:
    """Take a feature down. Costs no round — nothing was walked back.

    There is no delete. A feature's events are the only source of its rounds
    and its escapes, so removing one is not a cleanup but a rewrite of history;
    an abandoned feature stays in the population and out of every unfinished
    reading.
    """
    feature_id = _resolve(key, ref)
    _declare(feature_store.abandon, feature_id, session)
    typer.echo(f"#{feature_id}  {events.stage(feature_id)}")


@feature_app.command("refresh")
def refresh_cmd(
    key: str = typer.Argument(..., help="Feature id"),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference"),
) -> None:
    """Pull a remote feature's state, once, and fold it into the log.

    An event, never a poll: a mapped state that differs from the last event
    appends one row, and one that does not appends nothing. A native feature
    has nothing to fetch — its stage is derived here.
    """
    feature_id = _resolve(key, ref)
    try:
        error = sync_module.refresh(feature_id)
    except sync_module.NotRemote as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    if error:
        typer.echo(f"unchanged — {error}", err=True)
        raise typer.Exit(1)
    typer.echo(f"#{feature_id}  {events.stage(feature_id)}")
