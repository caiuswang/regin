"""`regin goal preflight "<goal>"` — emit the pre-build roadmap.

The portable front half of the `/goal-verified` loop: emits the universal
hard gates that decide "done" plus (opt-in) the past lessons recalled for
the goal, so the bar is pinned before any code is written. Per-area
convention skills/references are NOT routed here — they come from the
file-keyed table in CLAUDE.local.md. See `lib/goal_preflight.py` for the why.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import typer


goal_app = typer.Typer(
    name="goal",
    help="Loop-engineering helpers (roadmap preflight for /goal-verified)",
    no_args_is_help=True,
)


@goal_app.command(
    "preflight",
    help="Emit the roadmap (hard gates + recalled lessons) for a goal",
)
def cmd_goal_preflight(
    goal: str = typer.Argument(..., help="The freeform goal string"),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
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

    roadmap = build_roadmap(goal, with_lessons=with_lessons)
    offered = record_offered(session_id, roadmap.lessons, goal)
    get_activity_logger("goal").write(
        "preflight_emitted", gate_count=len(roadmap.gates),
        lesson_count=len(roadmap.lessons), offered_recorded=offered)

    # Empty-goal guard: warn on stderr so stdout stays clean (the roadmap
    # markdown / `--json` payload remains the only thing on stdout).
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
    topic: list[str] = typer.Option(
        None, "--topic",
        help="Authoritative topic short-path (the node id you walked the tree "
             "to) to file every new failure-lesson under (repeatable). Pass "
             "'none' (or '-') when the tree dead-ended and no node fits — the "
             "lesson is filed unbound instead of being misrouted by keyword guess"),
    trace_id: str = typer.Option(None, "--trace-id", help="Originating session trace id"),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from lib.activity_log import get_activity_logger
    from lib.goal_feedback import outcome_to_dict, record_outcome, render_summary

    result = record_outcome(
        goal, included_ids=included, offered_ids=offered, failures=fail,
        tags=tag, topics=topic, trace_id=trace_id)
    get_activity_logger("goal").write(
        "feedback_recorded", reinforced=len(result.reinforced),
        new_lessons=len(result.new_lessons),
        linked_topics=len(result.linked_topics))

    if json:
        print(_json.dumps(outcome_to_dict(result), indent=2))
    else:
        print(render_summary(result))


@goal_app.command(
    "spawn",
    help="Run a /goal-verified agent-arm worker (refiner|builder|verifier) "
         "as a subprocess — the portable form of the subagent dispatch",
)
def cmd_goal_spawn(
    role: str = typer.Argument(..., help="refiner | builder | verifier"),
    task: str = typer.Option(
        None, "--task",
        help="The worker's payload: goal + approved roadmap + recall block "
             "(+ the diff, for the verifier)"),
    task_file: str = typer.Option(
        None, "--task-file",
        help="Read the payload from a file instead of --task ('-' for stdin)"),
    agent: str = typer.Option(
        None, "--agent",
        help="Which configured external agent to run as "
             "(key in `topic_proposal_external_agents`); default: the first"),
    cwd: str = typer.Option(
        None, "--cwd",
        help="Working directory for the worker; default: the current one"),
    session_id: str = typer.Option(
        None, "--session-id",
        help="Session id to stamp the worker with (default: a generated one, "
             "printed on stderr so you can gate on the worker's own spans)"),
    timeout: int = typer.Option(
        None, "--timeout", help="Override the agent's configured timeout (s)"),
    print_prompt: bool = typer.Option(
        False, "--print-prompt",
        help="Render the composed worker prompt and exit without spawning"),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    import os
    import sys

    from lib.goal_spawn import (
        GoalSpawnError, compose_prompt, role_definition, spawn_role,
    )

    try:
        payload = _read_task(task, task_file)
        definition = role_definition(role)
        if print_prompt:
            print(compose_prompt(definition, payload))
            return
        result = spawn_role(
            role, payload, agent_id=agent, cwd=cwd or os.getcwd(),
            session_id=session_id, timeout=timeout)
    except GoalSpawnError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1)

    if json:
        print(_json.dumps({
            "role": result.role,
            "agent": result.agent_id,
            "session_id": result.session_id,
            "allowed_tools": list(result.allowed_tools),
            "stdout": result.stdout,
        }, indent=2))
        return
    # The worker's output is the only thing on stdout, so `$(regin goal spawn
    # …)` is the verdict; the attribution header goes to stderr.
    typer.echo(
        f"worker session: {result.session_id} "
        f"(role={result.role}, agent={result.agent_id})", err=True)
    sys.stdout.write(result.stdout)


def _read_task(task: str | None, task_file: str | None) -> str:
    """The worker payload from --task, --task-file, or stdin ('-')."""
    import sys

    from lib.goal_spawn import GoalSpawnError

    if task_file:
        if task_file == "-":
            return sys.stdin.read()
        path = Path(task_file)
        if not path.is_file():
            raise GoalSpawnError(f"--task-file not found: {task_file}")
        return path.read_text(encoding="utf-8")
    if task:
        return task
    raise GoalSpawnError("pass the worker's payload with --task or --task-file")


def register(app: typer.Typer) -> None:
    app.add_typer(goal_app)
