"""`regin defects` — the defect ledger.

A defect is something a feature got wrong, and every row is here because
somebody opened one. Nothing infers a defect from what anyone typed; an earlier
version matched regexes against prose and was deleted, because an inferred row
makes the metric measure the inference.

`--feature` names **the feature that caused the bug**, which is not necessarily
the one this session was working on — the two come apart constantly. Omit it
when unsure: unattributed is a first-class state, and a wrong parent inflates
one feature's rounds while exonerating another.

It takes a feature **id**. `--ref` is the same argument spelled as a remote
tracker's identifier, for the case where that is what the operator has in front
of them; it resolves to the one feature row carrying that reference, and
refuses rather than creating one.

Rounds-to-done lives on `regin defects rounds <ID>`, counted from the feature's
own event log. `Defect.round` is a different number — fix attempts on one
defect — and is never aggregated to a feature. See `docs/feature-pipeline.md`.
"""

from __future__ import annotations

from typing import Optional

import typer

from lib.defects import store
from lib.features import attribution, events
from lib.features import store as feature_store

#: Resolve the feature the session itself declared. Typing this is an
#: *affirmation* of that declaration, which is a declaration; regin filling the
#: field in because a row happened to exist would be an inference.
AT_SESSION = "@session"


def _declared(session: Optional[str], row, action: str) -> None:
    """Leave the span `regin gate pipeline-ran` counts — declaring a defect is
    engaging the pipeline just as much as asking for a stage is."""
    if not session:
        return
    from lib.hook_plugin import post_span
    post_span(trace_id=session, name="defect.declared",
              attributes={"action": action, "defect_id": row.id,
                          "feature_id": row.feature_id or 0,
                          "round": row.round})


def _resolve_feature(feature: Optional[str], ref: Optional[str],
                     session: Optional[str]) -> Optional[int]:
    """The feature id an operator named, however they spelled it.

    Three spellings, one answer: an id, a remote reference, or `@session` for
    the one this session declared. `@session` refuses rather than falling back
    to unattributed — the operator typed it specifically to attribute, and
    would otherwise walk away believing it landed.
    """
    if feature is None and ref is None:
        return None
    if ref is not None:
        return _by_ref(ref)
    if feature != AT_SESSION:
        return _as_id(feature)
    if not session:
        typer.echo(f"{AT_SESSION} needs --session to resolve against", err=True)
        raise typer.Exit(2)
    try:
        return attribution.declared_feature(session)
    except attribution.NoDeclaration as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)


def _as_id(feature: str) -> int:
    try:
        return int(feature)
    except (TypeError, ValueError):
        typer.echo(f"{feature!r} is not a feature id — `--ref {feature}` if "
                   "that is a tracker reference", err=True)
        raise typer.Exit(2)


def _by_ref(ref: str) -> int:
    """The feature carrying a remote reference, or a refusal naming it.

    Refuses rather than creating one: a typo would otherwise mint a second
    feature that looks like the first, which is the corrupted-data-that-looks-
    fine outcome this ledger exists to avoid.
    """
    from lib.tasks.integrations import IntegrationError, registry  # noqa: PLC0415

    # `name_ref`, not `resolve_ref`: this resolves to a LOCAL row, so
    # demanding a live credential would refuse a lookup that needs no network.
    try:
        parsed = registry.name_ref(ref)
    except IntegrationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    row = feature_store.by_external(parsed.provider, parsed.key)
    if row is None:
        typer.echo(f"no feature carries {parsed.key} — adopt it first", err=True)
        raise typer.Exit(2)
    return row.id


def _hint_declaration(session: Optional[str]) -> None:
    """Name what the session declared, so the shorthand is discoverable at the
    moment it is wanted — and go no further than naming it."""
    if not session:
        return
    rows = attribution.features_for_session(session)
    if not rows:
        return
    named = ", ".join(str(r.feature_id) for r in rows)
    typer.echo(f"  still unattributed. This session declared {named} — "
               f"`--feature {AT_SESSION}` if that is also what caused it.")


def _named(feature_id: Optional[int]) -> str:
    if feature_id is None:
        return "unattributed"
    row = feature_store.get(feature_id)
    return f"#{feature_id} {row.title}" if row else f"#{feature_id}"


defects_app = typer.Typer(
    name="defects",
    help="The defect ledger: what a feature got wrong, and what escaped",
    no_args_is_help=True,
)


@defects_app.command("open")
def open_cmd(
    symptom: str = typer.Argument(..., help="What went wrong, in your words"),
    feature: Optional[str] = typer.Option(
        None, "--feature", help="Feature id, or `@session` for the one this "
                                "session declared. Omit when unsure — "
                                "unattributed is a first-class state, and a "
                                "wrong parent is worse than no parent."),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference "
                            "(e.g. CAI-274) instead of its id"),
    session: Optional[str] = typer.Option(
        None, "--session", help="Trace id this was reported in — also leaves "
                                "the span `regin gate pipeline-ran` counts"),
    path: list[str] = typer.Option(
        [], "--path", help="A suspect file (repeatable). A hint, not a claim."),
) -> None:
    """Record a defect.

    Re-reporting the same symptom says so rather than silently returning the
    existing row: the caller is usually an agent, and a silent no-op read as a
    successful open is how a session ends up believing it filed something it
    did not.
    """
    resolved = _resolve_feature(feature, ref, session)
    try:
        result = store.record_reporting(symptom, feature_id=resolved,
                                        session_id=session, suspect_paths=path)
    except store.DefectError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    row = result.defect
    suffix = "  (already open — same symptom this session)" if result.deduped \
        else ""
    typer.echo(f"#{row.id} {_named(row.feature_id)} round={row.round} "
               f"{row.status}{suffix}")
    if row.feature_id is None:
        _hint_declaration(session)
    _declared(session, row, "opened")


def _scope(feature_id: Optional[int], fix_task: Optional[int], every: bool):
    if fix_task is not None:
        return store.by_fix_item(fix_task)
    if feature_id is not None:
        return store.for_feature(feature_id)
    return store.every() if every else store.unattributed()


def _nothing(feature_id: Optional[int], fix_task: Optional[int],
             every: bool) -> str:
    """Each scope says what its own emptiness means. "awaiting attribution"
    is a claim about the unattributed scope, and announcing it after a
    fix-item query described a scope that was never asked for."""
    if fix_task is not None:
        return f"no defects point at task {fix_task}"
    if feature_id is not None:
        return f"no defects for feature {feature_id}"
    return "no defects" if every else "no defects awaiting attribution"


def _line(row, every: bool, states: Optional[dict]) -> str:
    # Which feature is blamed is what `--all` is read for: a ledger-wide list
    # whose rows do not say cannot be acted on.
    where = f" {_named(row.feature_id)[:28]:<28}" if every else ""
    line = (f"#{row.id:<5} round={row.round} {row.status:<7}{where} "
            f"{row.symptom[:88]}")
    if states is None:
        return line
    pointer = f"task {row.fix_task_id}" if row.fix_task_id else "—"
    return line + f"\n      fix item {pointer} — {states.get(row.id, '—')}"


@defects_app.command("list")
def list_cmd(
    feature: Optional[str] = typer.Option(
        None, "--feature", help="Feature id"),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference"),
    fix_item: Optional[int] = typer.Option(
        None, "--fix-item", help="Every defect one task is meant to close — "
                                 "the batch-fix query"),
    every: bool = typer.Option(
        False, "--all", help="The whole ledger, newest first, attributed or "
                             "not. Without it this lists only the "
                             "unattributed ones."),
    with_fix_state: bool = typer.Option(
        False, "--with-fix-state",
        help="Read each fix task's current status. A local read now, so there "
             "is no unreachable-tracker reading to report."),
) -> None:
    """List a feature's defects, the whole ledger, or every unattributed one."""
    feature_id = _resolve_feature(feature, ref, None)
    rows = _scope(feature_id, fix_item, every)
    if not rows:
        typer.echo(_nothing(feature_id, fix_item, every))
        return
    states = store.fix_states(rows) if with_fix_state else {}
    for row in rows:
        typer.echo(_line(row, every, states if with_fix_state else None))


@defects_app.command("rounds")
def rounds_cmd(
    feature: str = typer.Argument(..., help="Feature id"),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference"),
) -> None:
    """Rounds-to-done: one build, plus every completion later walked back.

    Counted from the feature's own `feature_events`, so it is always
    answerable. It used to be read from a tracker's state history and could
    therefore fail to answer at all — which is why it once had to refuse
    rather than print `1`, since `1` is also what a real never-walked-back
    feature reads. With one local source there is no second reading to
    confuse it with.
    """
    typer.echo(events.rounds(_resolve_feature(feature, ref, None)))


@defects_app.command("attribute")
def attribute_cmd(
    defect_id: int = typer.Argument(..., help="Defect id"),
    feature: str = typer.Argument(
        ..., help=f"The feature that CAUSED the bug, or `{AT_SESSION}`"),
    ref: Optional[str] = typer.Option(
        None, "--ref", help="Name the feature by its tracker reference"),
    session: Optional[str] = typer.Option(
        None, "--session", help="This session's trace id"),
) -> None:
    """Late-bind a defect to the feature that caused it.

    This is also where the escape reading is decided: the defect is pointed at
    the feature's current last event, and that pointer never moves again unless
    blame moves to another feature.
    """
    resolved = _resolve_feature(feature, ref, session)
    try:
        row = store.attribute(defect_id, resolved)
    except store.DefectError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    blamed = events.last(row.feature_id)
    typer.echo(f"#{row.id} {_named(row.feature_id)} "
               f"blamed-against={blamed.kind if blamed else '—'}")
    _declared(session, row, "attributed")


@defects_app.command("close")
def close_cmd(
    defect_id: int = typer.Argument(..., help="Defect id"),
    check: str = typer.Option(..., "--check",
                              help="The check that would have caught this"),
    session: Optional[str] = typer.Option(
        None, "--session", help="This session's trace id"),
) -> None:
    """Close a defect, naming the check that would have caught it.

    Closing writes no attribution: it keeps one verb and one writer, and this
    is the moment the temptation to guess is strongest.
    """
    try:
        row = store.close(defect_id, missing_check=check)
    except store.DefectError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    typer.echo(f"#{row.id} closed — {row.missing_check}")
    if row.feature_id is None:
        typer.echo(f"  still unattributed — "
                   f"`regin defects attribute {row.id} <ID>`")
    _declared(session, row, "closed")


@defects_app.command("reopen")
def reopen_cmd(
    defect_id: int = typer.Argument(..., help="Defect id"),
    session: Optional[str] = typer.Option(
        None, "--session", help="This session's trace id"),
) -> None:
    """Record that we claimed this fixed and were wrong.

    A declared act, never inferred from a re-typed report: the round is what
    the whole rework reading hangs off.
    """
    try:
        row = store.reopen(defect_id)
    except store.DefectError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    typer.echo(f"#{row.id} reopened round={row.round}")
    _declared(session, row, "reopened")


@defects_app.command("merge")
def merge_cmd(
    duplicate_id: int = typer.Argument(..., help="The duplicate to fold away"),
    into: int = typer.Option(..., "--into", help="The defect to keep"),
    session: Optional[str] = typer.Option(
        None, "--session", help="This session's trace id"),
) -> None:
    """Fold a duplicate into a keeper, unioning its suspect paths.

    The duplicate row is deleted rather than closed: `close` demands a
    `missing_check`, and writing "duplicate of #17" into the ledger's only
    behaviour-changing field would be worse than losing the row.
    """
    try:
        row = store.merge(duplicate_id, into=into)
    except store.DefectError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    typer.echo(f"#{duplicate_id} merged into #{row.id}")
    _declared(session, row, "merged")


@defects_app.command("fix-item")
def fix_item_cmd(
    defect_id: int = typer.Argument(..., help="Defect id"),
    task: Optional[int] = typer.Option(
        None, "--task", help="An existing task id"),
    create: bool = typer.Option(
        False, "--create", help="Capture a task under the defect's feature "
                                "and point at it"),
    clear: bool = typer.Option(False, "--clear", help="Detach the pointer"),
    session: Optional[str] = typer.Option(
        None, "--session", help="This session's trace id"),
) -> None:
    """Point a defect at the task that will close it.

    A task id rather than the `(provider, key)` pair it replaces, so the loop
    closes in the schema and a reader can join it. Stated cost: a defect can no
    longer point at a remote issue that has no task here.

    Never a side effect of `defects open`: most defects are fixed inside the
    session that found them, so auto-creation would flood the board with items
    that are Done before anyone reads them. Nothing is counted from this
    pointer, and closing either side never touches the other.
    """
    if sum(map(bool, (task, create, clear))) != 1:
        typer.echo("name exactly one of --task, --create or --clear", err=True)
        raise typer.Exit(2)
    try:
        row = _write_fix_item(defect_id, task, create, clear)
    except store.DefectError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    typer.echo(f"#{row.id} fix item "
               f"{f'task {row.fix_task_id}' if row.fix_task_id else '—'}")
    _declared(session, row, "fix-item")


def _write_fix_item(defect_id: int, task, create, clear):
    if clear:
        return store.clear_fix_item(defect_id)
    if task:
        return store.set_fix_item(defect_id, task)
    return _create_fix_item(defect_id)


def _create_fix_item(defect_id: int):
    """Capture the task, then attach it — and never touch it again.

    Title is the symptom verbatim: the reporter's wording is stored verbatim
    for exactly this reason, and paraphrasing would make the board and the
    ledger describe the same bug differently. No label, assignee or priority —
    those are fields with human writers.
    """
    from lib.tasks import store as task_store  # noqa: PLC0415

    row = store.get(defect_id)
    if row.feature_id is None:
        raise store.DefectError(
            f"#{row.id} is unattributed, so there is no feature to file "
            f"under — `regin defects attribute {row.id} <ID>` first")
    view, _created = task_store.capture_task(
        title=row.symptom[:200], body=_back_pointer(row), source="defect",
        feature_id=row.feature_id)
    return store.set_fix_item(row.id, view["id"])


def _back_pointer(row) -> str:
    """What regin writes into the new task's body, and all it writes."""
    import json  # noqa: PLC0415
    paths = json.loads(row.suspect_paths_json or "[]")
    lines = [f"regin defect #{row.id}"]
    if row.reported_by_trace:
        lines.append(f"reported in session {row.reported_by_trace}")
    if paths:
        lines.append("suspect paths: " + ", ".join(paths))
    return "\n".join(lines)
