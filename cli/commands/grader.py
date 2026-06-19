"""`regin grade` — CLI surface over the post-hoc session rubric grader.

Thin wrappers around the `lib.grader` facade: `run` grades one captured
session on both axes (`--tier` defaults to `auto`: mechanical screen
pass, escalating borderline sessions to the judge-assisted deep tier
when an external agent is configured), `show` prints the latest stored
reports, `list` surveys recent grades, `pareto` prints the
cost-per-correct-outcome aggregate. The engine itself is documented
under *Session Grader* in `ARCHITECTURE.md`.
"""

from __future__ import annotations

import json
from typing import List, Optional

import typer

grade_app = typer.Typer(name="grade", help="Post-hoc session rubric grades",
                        no_args_is_help=True)


@grade_app.command("run")
def cmd_run(
    trace_id: str = typer.Argument(..., help="Session (trace) id to grade"),
    tier: str = typer.Option(
        "auto", "--tier",
        help="screen (mechanical) | deep (judge-assisted) | auto "
             "(screen, escalate borderline sessions to deep)"),
    axis: Optional[List[str]] = typer.Option(
        None, "--axis", help="Grade only these axes (repeatable): "
        "correctness | process. Default: both."),
    aspect: Optional[List[str]] = typer.Option(
        None, "--aspect", help="Gradeable aspect key to grade as its own "
        "dimension, repeatable (e.g. completeness, safety). Deep/auto only — "
        "each gets a satisfied/needs_revision/fail verdict."),
    distill: Optional[bool] = typer.Option(
        None, "--distill/--no-distill",
        help="On a flagged session, distill findings into lessons. "
             "Default: settings.grader.distill_on_fail."),
    no_persist: bool = typer.Option(False, "--no-persist",
                                    help="Print without storing"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    import lib.grader as grader
    from lib.grader.service import AXES, GradingError

    bad = [a for a in (axis or []) if a not in AXES]
    if bad:
        print(f"error: axis must be one of {list(AXES)}")
        raise typer.Exit(1)
    axes = tuple(dict.fromkeys(axis)) if axis else AXES
    try:
        result = grader.grade_session(trace_id, axes=axes, tier=tier,
                                      persist=not no_persist,
                                      distill=distill,
                                      aspects=list(aspect) if aspect else None)
    except GradingError as exc:
        print(f"error: {exc}")
        raise typer.Exit(1)
    if json_out:
        print(json.dumps(result, indent=2))
        return
    for name, grade in result["grades"].items():
        print(f"── {name} ({grade['tier']}, judge={grade['judge']}) ──")
        print(grade["report"])
        print()


@grade_app.command("show")
def cmd_show(
    trace_id: str = typer.Argument(..., help="Session (trace) id"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    import lib.grader as grader

    grades = grader.latest_grades(trace_id, with_detail=json_out)
    if not grades:
        print("no grades stored for this session — run `regin grade run`")
        raise typer.Exit(1)
    if json_out:
        print(json.dumps(grades, indent=2))
        return
    for name, grade in grades.items():
        print(f"── {name} ({grade['tier']}, {grade['created_at']}) ──")
        print(grade["report"])
        print()


@grade_app.command("list")
def cmd_list(
    limit: int = typer.Option(20, "--limit"),
    axis: Optional[str] = typer.Option(None, "--axis"),
    verdict: Optional[str] = typer.Option(None, "--verdict"),
    include_tests: bool = typer.Option(False, "--include-tests"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    import lib.grader as grader

    rows = grader.list_grades(limit=limit, axis=axis, verdict=verdict,
                              include_tests=include_tests)
    if json_out:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("no grades stored yet")
        raise typer.Exit(1)
    for row in rows:
        title = (row["session"].get("title") or row["trace_id"])[:48]
        print(f"  {row['trace_id'][:8]}  {row['axis']:12s} "
              f"{row['verdict']:14s} {row['tier']:6s} "
              f"{row['created_at']:20s} {title}")


@grade_app.command("reflect")
def cmd_reflect(
    min_sessions: Optional[int] = typer.Option(
        None, "--min-sessions",
        help="Recurrence threshold (default: grader.aggregate_min_sessions)"),
    limit: int = typer.Option(
        200, "--limit", help="Failing sessions to scan"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report recurring modes without writing"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Consolidate recurring cross-session failure modes into agent-memory
    lessons (one per mode, refreshed in place on re-runs). Needs agent
    memory enabled; consolidated lessons land in the review queue."""
    import lib.grader as grader

    result = grader.aggregate_failure_modes(
        limit_sessions=limit, min_sessions=min_sessions, persist=not dry_run)
    if json_out:
        print(json.dumps(result.__dict__, indent=2))
        return
    print(f"scanned {result.trace_count} failing sessions — "
          f"{result.recurring} recurring mode(s); "
          f"{result.created} created, {result.refreshed} refreshed")
    for mode in result.modes:
        mid = (mode.get("memory_id") or "")[:8]
        print(f"  {mode['mode']:34s} {mode['sessions']:3d} sessions  {mid}")


@grade_app.command("pareto")
def cmd_pareto(
    include_tests: bool = typer.Option(False, "--include-tests"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    import lib.grader as grader

    data = grader.pareto_points(include_tests=include_tests)
    if json_out:
        print(json.dumps(data, indent=2))
        return
    summary = data["summary"]
    print(f"sessions graded: {summary['sessions']}  "
          f"satisfied: {summary['satisfied']}  "
          f"cost/correct-outcome: {summary['cost_per_correct_outcome']}")
    flagged = [p for p in data["points"]
               if p["cheaply_wrong"] or p["expensively_right"]]
    for point in flagged:
        flag = "cheaply-wrong" if point["cheaply_wrong"] else "expensively-right"
        print(f"  {point['trace_id'][:8]}  {flag:18s} "
              f"${point['cost_usd']:.2f}  {point['title'] or ''}")
