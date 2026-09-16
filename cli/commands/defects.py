"""`regin defects` — the defect ledger.

A defect is something a feature got wrong, and every row is here because
somebody opened one. Nothing infers a defect from what anyone typed; an earlier
version matched regexes against prose and was deleted, because an inferred row
makes the metric measure the inference.

`--feature` names **the feature that caused the bug**, which is not necessarily
the one this session was working on — the two come apart constantly. Omit it
when unsure: unattributed is a first-class state, and a wrong parent inflates
one feature's rounds while exonerating another.

Rounds-to-done lives on `regin defects rounds <KEY>`, read from the tracker's
own state history. `Defect.round` is a different number — fix attempts on one
defect — and is never aggregated to a feature. See `docs/feature-pipeline.md`.
"""

from __future__ import annotations

from typing import Optional

import typer

from lib.defects import store, tracker
from lib.features import attribution

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
                          "feature_key": row.feature_key or "",
                          "round": row.round})


def _resolve_feature(feature: Optional[str], session: Optional[str]) -> Optional[str]:
    """Expand `@session`, or refuse loudly.

    Refusing beats falling back to unattributed: the operator typed something
    specifically to attribute and would otherwise walk away believing it landed.
    """
    if feature != AT_SESSION:
        return feature
    if not session:
        typer.echo(f"{AT_SESSION} needs --session to resolve against", err=True)
        raise typer.Exit(2)
    try:
        return attribution.declared_feature(session)
    except attribution.NoDeclaration as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)


def _hint_declaration(session: Optional[str]) -> None:
    """Name what the session declared, so the shorthand is discoverable at the
    moment it is wanted — and go no further than naming it."""
    if not session:
        return
    rows = attribution.features_for_session(session)
    if not rows:
        return
    keys = ", ".join(r.feature_key for r in rows)
    typer.echo(f"  still unattributed. This session declared {keys} — "
               f"`--feature {AT_SESSION}` if that is also what caused it.")


defects_app = typer.Typer(
    name="defects",
    help="The defect ledger: what a feature got wrong, and what escaped",
    no_args_is_help=True,
)


@defects_app.command("open")
def open_cmd(
    symptom: str = typer.Argument(..., help="What went wrong, in your words"),
    feature: Optional[str] = typer.Option(
        None, "--feature", help="Feature key, e.g. CAI-206, or `@session` for "
                                "the one this session declared. Omit when "
                                "unsure — unattributed is a first-class "
                                "state, and a wrong parent is worse than no "
                                "parent."),
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
    resolved = _resolve_feature(feature, session)
    try:
        result = store.record_reporting(symptom, feature_key=resolved,
                                        session_id=session, suspect_paths=path)
    except store.DefectError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    row = result.defect
    where = row.feature_key or "unattributed"
    suffix = "  (already open — same symptom this session)" if result.deduped \
        else ""
    typer.echo(f"#{row.id} {where} round={row.round} {row.status}{suffix}")
    if row.feature_key is None:
        _hint_declaration(session)
    _declared(session, row, "opened")


def _scope(feature: Optional[str], fix_item: Optional[str], every: bool):
    if fix_item:
        # The PARSED key, not the text that named it. `fix-item --issue`
        # stores what the tracker resolved, so querying the raw string meant
        # every spelling the write accepts — a pasted URL, a lowercase ref —
        # read back as "nothing points at this item" for rows that do.
        ref = tracker.name_ref(fix_item)
        return store.by_fix_item(ref.provider, ref.key)
    if feature:
        return store.for_feature(feature)
    return store.every() if every else store.unattributed()


def _nothing(feature: Optional[str], fix_item: Optional[str],
             every: bool) -> str:
    """Each scope says what its own emptiness means. "awaiting attribution"
    is a claim about the unattributed scope, and announcing it after a
    fix-item query described a scope that was never asked for."""
    if fix_item:
        return f"no defects point at {fix_item}"
    if feature:
        return f"no defects for {feature}"
    return "no defects" if every else "no defects awaiting attribution"


def _line(row, every: bool, states: Optional[dict]) -> str:
    # The key is what `--all` is read for: a ledger-wide list whose rows do not
    # say who is blamed cannot be acted on.
    where = f" {row.feature_key or 'unattributed':<12}" if every else ""
    line = (f"#{row.id:<5} round={row.round} {row.status:<7}{where} "
            f"{row.symptom[:88]}")
    if states is None:
        return line
    pointer = row.fix_external_key or "—"
    return line + f"\n      fix item {pointer} — {states.get(row.id, '—')}"


@defects_app.command("list")
def list_cmd(
    feature: Optional[str] = typer.Option(
        None, "--feature", help="Feature key, e.g. CAI-206"),
    fix_item: Optional[str] = typer.Option(
        None, "--fix-item", help="Every defect one tracker item is meant to "
                                 "close — the batch-fix query"),
    every: bool = typer.Option(
        False, "--all", help="The whole ledger, newest first, attributed or "
                             "not. Without it this lists only the "
                             "unattributed ones."),
    with_fix_state: bool = typer.Option(
        False, "--with-fix-state",
        help="Read each fix item's current state from the tracker. Reads "
             "`unknown` when none is reachable, never \"in sync\"."),
) -> None:
    """List a feature's defects, the whole ledger, or every unattributed one."""
    rows = _scope(feature, fix_item, every)
    if not rows:
        typer.echo(_nothing(feature, fix_item, every))
        return
    states = store.fix_states(rows) if with_fix_state else {}
    for row in rows:
        typer.echo(_line(row, every, states if with_fix_state else None))


@defects_app.command("rounds")
def rounds_cmd(feature: str = typer.Argument(..., help="Feature key")) -> None:
    """Rounds-to-done: one build, plus every completion later walked back.

    Read from the tracker's recorded state history, because only a declared
    state transition witnesses a completion. When no tracker can answer — it
    is unreachable, or it holds no such issue — this reports that rather than
    a number: `1` is what a real never-reopened feature looks like, so it must
    not double as "could not look".

    A native feature reads `1 + completions_walked_back` from its own row, so
    its `1` is always the first reading and never the second."""
    try:
        typer.echo(tracker.rounds_for(feature))
    except tracker.TrackerUnavailable as exc:
        typer.echo(f"unknown — {exc}", err=True)
        raise typer.Exit(2)


@defects_app.command("attribute")
def attribute_cmd(
    defect_id: int = typer.Argument(..., help="Defect id"),
    feature: str = typer.Argument(
        ..., help=f"The feature that CAUSED the bug, or `{AT_SESSION}`"),
    session: Optional[str] = typer.Option(
        None, "--session", help="This session's trace id"),
) -> None:
    """Late-bind a defect to the feature that caused it.

    This is also where the escape reading is decided: the tracker is asked once
    what state the feature occupies, and the raw answer is frozen onto the row.
    """
    resolved = _resolve_feature(feature, session)
    try:
        row = store.attribute(defect_id, resolved)
    except store.DefectError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    typer.echo(f"#{row.id} {row.feature_key} "
               f"blamed-against={row.blamed_against_state or 'unknown'}")
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

    Closing writes no `feature_key`: attribution keeps one verb and one writer,
    and this is the moment the temptation to guess is strongest.
    """
    try:
        row = store.close(defect_id, missing_check=check)
    except store.DefectError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    typer.echo(f"#{row.id} closed — {row.missing_check}")
    if row.feature_key is None:
        typer.echo(f"  still unattributed — "
                   f"`regin defects attribute {row.id} <KEY>`")
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
    issue: Optional[str] = typer.Option(
        None, "--issue", help="An existing tracker item, e.g. CAI-241"),
    create: bool = typer.Option(
        False, "--create", help="File a sub-issue under the defect's feature "
                                "and attach it"),
    clear: bool = typer.Option(False, "--clear", help="Detach the pointer"),
    session: Optional[str] = typer.Option(
        None, "--session", help="This session's trace id"),
) -> None:
    """Point a defect at the tracker item that will close it.

    Never a side effect of `defects open`: most defects are fixed inside the
    session that found them, so auto-creation would flood the board with items
    that are Done before anyone reads them. Nothing is counted from this
    pointer, and closing either side never touches the other.
    """
    if sum(map(bool, (issue, create, clear))) != 1:
        typer.echo("name exactly one of --issue, --create or --clear",
                   err=True)
        raise typer.Exit(2)
    try:
        row = _write_fix_item(defect_id, issue, create, clear)
    except (store.DefectError, tracker.TrackerUnavailable) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2)
    typer.echo(f"#{row.id} fix item {row.fix_external_key or '—'}")
    _declared(session, row, "fix-item")


def _write_fix_item(defect_id: int, issue, create, clear):
    if clear:
        return store.clear_fix_item(defect_id)
    if issue:
        # Named, not resolved: recording where a fix will land needs no
        # credential, and regin never validates the pointer anyway.
        ref = tracker.name_ref(issue)
        return store.set_fix_item(defect_id, ref.provider, ref.key)
    return _create_fix_item(defect_id)


def _create_fix_item(defect_id: int):
    """File the sub-issue, then attach it — and never touch it again.

    Title is the symptom verbatim: the reporter's wording is stored verbatim
    for exactly this reason, and paraphrasing would make the board and the
    ledger describe the same bug differently. No label, assignee or priority —
    those are tracker fields with human writers.
    """
    row = store.get(defect_id)
    if not row.feature_key:
        raise store.DefectError(
            f"#{row.id} is unattributed, so there is no parent to file "
            f"under — `regin defects attribute {row.id} <KEY>` first")
    provider, parent = tracker.resolve(row.feature_key)
    from lib.tasks.integrations import IntegrationError  # noqa: PLC0415
    try:
        ref = provider.create_issue(parent=parent, title=row.symptom,
                                    body=_back_pointer(row))
    except IntegrationError as exc:
        raise store.DefectError(str(exc)) from exc
    return store.set_fix_item(row.id, ref.provider, ref.key)


def _back_pointer(row) -> str:
    """What regin writes into the new issue's body, and all it writes."""
    import json  # noqa: PLC0415
    paths = json.loads(row.suspect_paths_json or "[]")
    lines = [f"regin defect #{row.id}"]
    if row.session_id:
        lines.append(f"reported in session {row.session_id}")
    if paths:
        lines.append("suspect paths: " + ", ".join(paths))
    return "\n".join(lines)
