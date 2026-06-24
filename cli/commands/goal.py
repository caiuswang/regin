"""`regin goal preflight "<goal>"` — emit the pre-build roadmap.

The deterministic front half of the `/goal-verified` loop. Routes a
freeform goal to the skills, reference components, design tokens and
hard gates it must conform to, so the bar is pinned before any code is
written. See `lib/goal_preflight.py` for the why.
"""

from __future__ import annotations

import json as _json

import typer


goal_app = typer.Typer(
    name="goal",
    help="Loop-engineering helpers (roadmap preflight for /goal-verified)",
    no_args_is_help=True,
)


@goal_app.command(
    "preflight",
    help="Emit the roadmap (standards + references + gates) for a goal",
)
def cmd_goal_preflight(
    goal: str = typer.Argument(..., help="The freeform goal string"),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    repo_root: str = typer.Option(None, "--repo-root",
                                  help="Repo root for reference globbing (default: cwd)"),
    with_lessons: bool = typer.Option(
        False, "--with-lessons/--no-lessons",
        help="Recall past lessons into the roadmap via the legacy flat FTS leg. "
             "OFF by default (demoted 2026-06: ~22%% injection engagement). "
             "Lessons now come from structure-first `regin memory recall-for-task`; "
             "pass --with-lessons only to A/B the old flat recall."),
    session_id: str = typer.Option(
        None, "--session-id",
        help="Session id to record the offered lessons under (makes the "
             "engagement denominator automatic even if `goal feedback` is skipped)"),
) -> None:
    from lib.activity_log import get_activity_logger
    from lib.goal_preflight import (
        build_roadmap, record_offered, render_markdown, roadmap_to_dict,
        roadmap_warning,
    )

    roadmap = build_roadmap(goal, repo_root=repo_root, with_lessons=with_lessons)
    offered = record_offered(session_id, roadmap.lessons, goal)
    get_activity_logger("goal").write(
        "preflight_emitted", areas=roadmap.areas,
        skill_count=len(roadmap.skills), offered_recorded=offered)

    # Hollow-roadmap guard: warn on stderr so stdout stays clean (the
    # roadmap markdown / `--json` payload remains the only thing on stdout).
    warning = roadmap_warning(roadmap)
    if warning:
        typer.echo(f"warning: {warning}", err=True)

    if json:
        print(_json.dumps(roadmap_to_dict(roadmap), indent=2))
    else:
        print(render_markdown(roadmap))


@goal_app.command(
    "feedback",
    help="Record a /goal-verified outcome back into memory (reinforce + new lessons)",
)
def cmd_goal_feedback(
    goal: str = typer.Argument(..., help="The goal that was just verified"),
    included: list[str] = typer.Option(
        None, "--included", help="Lesson id folded into the approved roadmap (repeatable)"),
    offered: list[str] = typer.Option(
        None, "--offered", help="Lesson id preflight surfaced (repeatable)"),
    fail: list[str] = typer.Option(
        None, "--fail", help="An acceptance item that FAILED, phrased as a rule (repeatable)"),
    tag: list[str] = typer.Option(
        None, "--tag", help="Area tag for new failure-lessons, e.g. frontend (repeatable)"),
    trace_id: str = typer.Option(None, "--trace-id", help="Originating session trace id"),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from lib.activity_log import get_activity_logger
    from lib.goal_feedback import outcome_to_dict, record_outcome, render_summary

    result = record_outcome(
        goal, included_ids=included, offered_ids=offered, failures=fail,
        tags=tag, trace_id=trace_id)
    get_activity_logger("goal").write(
        "feedback_recorded", reinforced=len(result.reinforced),
        new_lessons=len(result.new_lessons))

    if json:
        print(_json.dumps(outcome_to_dict(result), indent=2))
    else:
        print(render_summary(result))


def register(app: typer.Typer) -> None:
    app.add_typer(goal_app)
