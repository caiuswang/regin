"""`regin workspace add / list / remove` — root directories grouping repos."""

from __future__ import annotations

import json as _json
from typing import Optional

import typer

from cli.deps import require_db


workspace_app = typer.Typer(
    name="workspace",
    help="Manage workspaces (a root directory whose members are the repos under it)",
    no_args_is_help=True,
)


@workspace_app.command("add", help="Create a workspace, registering the git repos at or under PATH")
@require_db
def cmd_add(
    path: str = typer.Argument(..., help="Absolute path of the workspace root"),
    name: Optional[str] = typer.Option(None, "--name", help="Defaults to the root's basename"),
) -> None:
    from lib.workspaces import WorkspaceError, create

    try:
        result = create(path, name)
    except WorkspaceError as exc:
        print(f"  error ({exc.field}): {exc}")
        raise typer.Exit(1)
    ws = result["workspace"]
    print(f"  added workspace {ws['name']} at {ws['root_path']} "
          f"({len(ws['members'])} repos, {len(result['registered'])} newly registered)")
    for s in result["skipped"]:
        print(f"  skipped {s['path']}: {s['reason']}")


@workspace_app.command("list", help="List workspaces with their member repos")
@require_db
def cmd_list(
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    from lib.workspaces import list_workspaces

    rows = list_workspaces()
    if json:
        print(_json.dumps(rows))
        return
    if not rows:
        print("  no workspaces")
        return
    for ws in rows:
        print(f"  {ws['name']}  {ws['root_path']}  ({len(ws['members'])} repos)")
        for m in ws["members"]:
            print(f"    - {m['name']}  {m['path']}")


@workspace_app.command("remove", help="Delete a workspace by name; its repos stay registered")
@require_db
def cmd_remove(
    name: str = typer.Argument(..., help="Workspace name"),
) -> None:
    from lib.workspaces import remove_by_name

    if remove_by_name(name) is None:
        print(f"  not found: {name}")
        raise typer.Exit(1)
    print(f"  removed workspace {name}")
