"""`regin task ...` — capture and inspect task ideas locally.

Local writes go straight through `lib/tasks/store.py`, not over HTTP: the CLI
already runs as the operator on the machine that owns the DB, so making it
mint and present an API key would only add a credential to lose. That also
means these commands work with the server stopped.

`finish` is the one exception, and for a reason worth stating: it is the
*running agent's* hand-back, and settling its attempt has to release whatever
depends on it — which means launching another agent, which only the server's
loop can own. A run launched from a CLI process dies when that process exits.
So `finish` posts to the local server instead of writing the DB, and a server
that is not there is a hard error rather than a silent half-settle: a task run
only exists inside `regin serve`, so it is always up when this is called.

Subcommands:
  add     — record an idea (always `status='idea'`)
  list    — the backlog, newest first
  show    — one task in full
  delete  — remove a task and everything keyed to it
  finish  — report a run's work done (the agent's own final step)

The done-claim is **not** here. It is an act on a feature, and a feature is its
own row now: `regin feature ship <ID>`. It lived on a task because a top-level
task used to BE the feature, which is exactly the overload regin:82 removed.
"""

from __future__ import annotations

import json as _json
import urllib.error
import urllib.request
from typing import List, Optional

import typer
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.orm.models.tasks import TASK_PRIORITIES
from lib.tasks import store


tasks_app = typer.Typer(
    name="task",
    help="Capture and inspect task ideas (agent_tasks)",
    no_args_is_help=True,
)


def _project_id(name: Optional[str]) -> Optional[int]:
    """Resolve a repo NAME to its id — a project is a registered repo.

    By name rather than by id because that is what an operator has in front of
    them; an unknown one lists the registered names instead of failing with a
    number they never typed.
    """
    if not name:
        return None
    with SessionLocal() as session:
        rows = session.exec(select(Repo).where(Repo.is_active == 1)).all()
        match = next((r for r in rows if r.name == name), None)
        if match is not None:
            return match.id
        known = ", ".join(sorted(r.name for r in rows)) or "none registered"
    print(f"no project (repo) named {name!r}. Registered: {known}")
    raise typer.Exit(code=1)


def _checked_priority(value: Optional[str]) -> str:
    if not value:
        return "none"
    if value not in TASK_PRIORITIES:
        print(f"unknown priority {value!r}. "
              f"One of: {', '.join(TASK_PRIORITIES)}")
        raise typer.Exit(code=1)
    return value


def _emit(payload, as_json: bool, render) -> None:
    if as_json:
        print(_json.dumps(payload))
    else:
        render(payload)


@tasks_app.command("add", help="Capture an idea.")
def cmd_add(
    title: str = typer.Argument(..., help="One-line title (≤200 chars)."),
    body: Optional[str] = typer.Option(
        None, "--body",
        help="Longer description — a feature's spec lives here."),
    tag: List[str] = typer.Option(
        [], "--tag", help="Repeatable tag."),
    project: Optional[str] = typer.Option(
        None, "--project",
        help="Registered repo name to file this under (see `regin repos`)."),
    priority: Optional[str] = typer.Option(
        None, "--priority",
        help=f"One of: {', '.join(TASK_PRIORITIES)}."),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    task, _ = store.capture_task(title=title[:200], body=(body or None),
                                 source="cli", tags=list(tag),
                                 project_id=_project_id(project),
                                 priority=_checked_priority(priority))
    _emit(task, json, lambda t: print(f"#{t['id']}  {t['title']}"))


@tasks_app.command("list", help="List captured tasks, newest first.")
def cmd_list(
    status: Optional[str] = typer.Option(
        "idea", "--status", help="Filter by status; empty string for all."),
    limit: int = typer.Option(50, "--limit", help="Max rows."),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    tasks = store.list_tasks(status=status or None, limit=limit)
    _emit(tasks, json, _render_list)


def _render_list(tasks: list) -> None:
    if not tasks:
        print("no tasks")
        return
    for task in tasks:
        print(f"#{task['id']:<5} {task['status']:<9} {task['source']:<10} "
              f"{task['title']}")


@tasks_app.command("show", help="Show one task in full.")
def cmd_show(
    task_id: int = typer.Argument(..., help="Task id."),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    task = store.get_task(task_id)
    if task is None:
        print(f"no task #{task_id}")
        raise typer.Exit(code=1)
    _emit(task, json, _render_task)


def _project_name(project_id) -> str:
    if project_id is None:
        return "—"
    with SessionLocal() as session:
        repo = session.get(Repo, project_id)
    return repo.name if repo is not None else f"#{project_id} (unregistered)"


def _render_task(task: dict) -> None:
    print(f"#{task['id']}  {task['title']}")
    print(f"status:      {task['status']}")
    print(f"priority:    {task['priority']}")
    print(f"project:     {_project_name(task['project_id'])}")
    print(f"source:      {task['source']}")
    print(f"external_id: {task['external_id'] or '—'}")
    print(f"tags:        {', '.join(task['tags']) or '—'}")
    print(f"created:     {task['created_at']}")
    if task["body"]:
        print()
        print(task["body"])


@tasks_app.command("delete", help="Delete a task and everything keyed to it.")
def cmd_delete(
    task_id: int = typer.Argument(..., help="Task id."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Remove the row, its attempts, images, links and dependency edges.

    Unlike archiving there is nothing left to restore, so the title is echoed
    back before the prompt — an operator confirming a bare id is confirming a
    number, not the task they meant.
    """
    task = store.get_task(task_id)
    if task is None:
        print(f"no task #{task_id}")
        raise typer.Exit(code=1)
    if not yes:
        typer.confirm(f"Delete #{task_id} {task['title']!r} for good?",
                      abort=True)
    try:
        deleted = store.delete_task(task_id)
    except store.DeleteRefused as exc:
        print(str(exc))
        raise typer.Exit(code=1)
    _emit(deleted, json, lambda t: print(f"deleted #{t['id']}  {t['title']}"))


@tasks_app.command(
    "finish",
    help="Report a task run's work finished (run BY the task's own agent).")
def cmd_finish(
    run_id: int = typer.Argument(..., help="Attempt id, baked into the prompt."),
    status: str = typer.Option(
        "done", "--status", help="done | failed."),
    reason: Optional[str] = typer.Option(
        None, "--reason", help="Why it failed; recorded on the attempt."),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Settle the attempt and release its dependents, without ending the
    session this runs inside.

    A non-2xx exits 1 with the server's message on stderr, because the agent
    calling this is still alive and can act on it — the same retry affordance
    `topics proposal-finish` gives a drafting agent whose ingest was refused.
    """
    from lib.settings import settings

    base = settings.agent_memory.inject_server_url.rstrip("/")
    url = f"{base}/api/tasks/runs/{run_id}/finish"
    body = _json.dumps({"status": status, "reason": reason or ""}).encode()
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = _json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = (exc.read() or b"").decode(errors="replace")
        typer.echo(f"task finish: server refused ({exc.code}): {detail}",
                   err=True)
        raise typer.Exit(code=1)
    except OSError as exc:
        typer.echo(f"task finish: cannot reach regin at {url}: {exc}", err=True)
        raise typer.Exit(code=1)
    _emit(result, json, _render_finish)


def _render_finish(result: dict) -> None:
    if not result.get("settled"):
        print(f"run #{result['run_id']} was already settled — nothing to do")
        return
    fired = result.get("fired") or []
    print(f"run #{result['run_id']} settled; task #{result['task_id']} closed")
    print(f"released: {', '.join(f'#{t}' for t in fired) if fired else '—'}")
