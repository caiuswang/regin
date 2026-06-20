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
) -> None:
    from lib.activity_log import get_activity_logger
    from lib.goal_preflight import (
        build_roadmap, render_markdown, roadmap_to_dict, roadmap_warning,
    )

    roadmap = build_roadmap(goal, repo_root=repo_root)
    get_activity_logger("goal").write(
        "preflight_emitted", areas=roadmap.areas, skill_count=len(roadmap.skills))

    # Hollow-roadmap guard: warn on stderr so stdout stays clean (the
    # roadmap markdown / `--json` payload remains the only thing on stdout).
    warning = roadmap_warning(roadmap)
    if warning:
        typer.echo(f"warning: {warning}", err=True)

    if json:
        print(_json.dumps(roadmap_to_dict(roadmap), indent=2))
    else:
        print(render_markdown(roadmap))


def register(app: typer.Typer) -> None:
    app.add_typer(goal_app)
