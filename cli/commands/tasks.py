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
  comment — reply into a comment thread, and optionally settle it
  arm     — set a task's launch parameters (server-first, like finish)
  finish  — report a run's work done (the agent's own final step)

The done-claim is **not** here. It is an act on a feature, and a feature is its
own row now: `regin feature ship <ID>`. It lived on a task because a top-level
task used to BE the feature, which is exactly the overload regin:82 removed.
"""

from __future__ import annotations

import json as _json
import os
import sys
import urllib.error
import urllib.request
from typing import List, Optional

import typer
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models.sync import Repo
from lib.orm.models.comments import (
    AUTHOR_AGENT, RESOLUTION_OPEN, RESOLUTION_RESOLVED, SUBJECT_TASK)
from lib.orm.models.tasks import TASK_KIND_TASK, TASK_KINDS, TASK_PRIORITIES
from lib.tasks import store
from lib.tasks.commands import (
    DRAFT_EXIT, CommandsAbsent, CommandsRejected, load_project_commands,
    print_draft,
)


# Its own exit code, like the command table's draft: a run that refused a
# slice wrote the rest, and a caller has to tell that apart from both a clean
# pass and a wholesale refusal without parsing prose.
REFUSED_SLICE_EXIT = 3

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
    kind: str = typer.Option(
        TASK_KIND_TASK, "--kind",
        help=f"What the step is. One of: {', '.join(TASK_KINDS)}."),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    if kind not in TASK_KINDS:
        print(f"unknown kind {kind!r}. One of: {', '.join(TASK_KINDS)}")
        raise typer.Exit(code=1)
    task, _ = store.capture_task(title=title[:200], body=(body or None),
                                 source="cli", tags=list(tag),
                                 project_id=_project_id(project),
                                 priority=_checked_priority(priority),
                                 kind=kind)
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
    "comment",
    help="Reply into a task's comment thread (run BY the task's own agent).")
def cmd_comment(
    thread_id: int = typer.Argument(
        ..., help="Thread id, as the run's <followup> block labels it."),
    body: str = typer.Option(..., "--body", help="What you did about it."),
    resolve: bool = typer.Option(
        False, "--resolve",
        help="Settle the thread so it stops reaching later runs."),
    reopen: bool = typer.Option(
        False, "--reopen", help="Re-open a settled thread."),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Post an agent's answer into a thread, and settle it in the same act.

    A local write, so it goes straight through the store like every other
    command here — `finish` is the lone exception, and only because settling an
    attempt has to launch whatever depends on it.

    Resolving is the same call the task page makes: it says the conversation is
    dealt with, not that the task is done. Without `--resolve` the thread stays
    open and is delivered to the next run — which is how an agent hands a
    question back.
    """
    from lib.comments import store as comments

    if resolve and reopen:
        _refuse("--resolve and --reopen are opposites", 2)
    thread = _task_thread(thread_id)
    try:
        comment = comments.add_comment(thread_id, body=body,
                                       author_kind=AUTHOR_AGENT)
    except comments.CommentRejected as exc:
        _refuse(str(exc), 2)
    if comment is None:                        # deleted between the two reads
        _refuse(f"no thread #{thread_id}", 1)
    if resolve or reopen:
        thread = comments.resolve(
            thread_id, RESOLUTION_RESOLVED if resolve else RESOLUTION_OPEN)
    _emit({"comment": comment, "thread_id": thread_id,
           "resolution_state": (thread or {}).get("resolution_state")
           or RESOLUTION_OPEN},
          json, _render_comment)


def _refuse(message: str, code: int) -> None:
    typer.echo(f"task comment: {message}", err=True)
    raise typer.Exit(code=code)


def _task_thread(thread_id: int) -> dict:
    """The thread, once it is proved to be one this command may write to.

    An id off a prompt is not proof of what it belongs to, and a mistyped one
    would otherwise post as `agent` into somebody else's conversation.
    """
    from lib.comments import store as comments

    thread = comments.get_thread(thread_id)
    if thread is None:
        _refuse(f"no thread #{thread_id}", 1)
    if thread["subject_kind"] != SUBJECT_TASK:
        _refuse(f"thread #{thread_id} is on a {thread['subject_kind']}, "
                "not a task", 2)
    return thread


def _render_comment(payload: dict) -> None:
    state = payload["resolution_state"]
    print(f"comment #{payload['comment']['id']} posted to thread "
          f"#{payload['thread_id']} ({state})")
    if state == RESOLUTION_OPEN:
        print("still open — it reaches the next run until someone resolves it")


@tasks_app.command(
    "finish",
    help="Report a task run's work finished (run BY the task's own agent).")
def cmd_finish(
    task_id: int = typer.Argument(..., help="Task id, baked into the prompt."),
    run_id: Optional[int] = typer.Option(
        None, "--run", help="Attempt id of that task, baked into the prompt. "
                            "Absent, the argument is the attempt id."),
    status: str = typer.Option(
        "done", "--status", help="done | failed."),
    reason: Optional[str] = typer.Option(
        None, "--reason",
        help="What this run did and why. Recorded on the attempt AND posted "
             "on the task as the run's report."),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Settle the attempt and release its dependents, without ending the
    session this runs inside.

    A non-2xx exits 1 with the server's message on stderr, because the agent
    calling this is still alive and can act on it — the same retry affordance
    `topics proposal-finish` gives a drafting agent whose ingest was refused.
    """
    from lib.tasks import finish_report

    # Prompts issued before the task id led the command still read
    # `task finish <run id>`, and those sessions are alive to run it.
    if run_id is None:
        run_id, task_id = task_id, None
    try:
        result = finish_report.report_finish(
            task_id, run_id, status, reason or "", opener=_open_loopback,
            on_local=lambda: typer.echo(
                "task finish: regin is not running — settling here",
                err=True))
    except finish_report.ReportRefused as exc:
        typer.echo(f"task finish: {exc}", err=True)
        raise typer.Exit(code=1) from None
    _emit(result, json, _render_finish)


@tasks_app.command("arm", help="Arm a task with its launch parameters.")
def cmd_arm(
    task_id: int = typer.Argument(..., help="Task id."),
    cwd: Optional[str] = typer.Option(None, "--cwd", help="Working directory."),
    branch: Optional[str] = typer.Option(
        None, "--branch", help="Run in a worktree on this branch."),
    base: Optional[str] = typer.Option(
        None, "--base", help="Ref a new --branch forks from."),
    provider: Optional[str] = typer.Option(None, "--provider"),
    model: Optional[str] = typer.Option(None, "--model"),
    launch: Optional[str] = typer.Option(None, "--launch", help="sdk | tmux."),
    skill: Optional[str] = typer.Option(
        None, "--skill", help="Slash command the run opens with."),
    permission_mode: Optional[str] = typer.Option(None, "--permission-mode"),
    effort: Optional[str] = typer.Option(None, "--effort"),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """POST to the server when it is up, so a task whose dependencies already
    landed is released by the loop that owns launches; otherwise arm here.
    Both paths run the same `parse_arm` → `check_target`. A refusal exits 1
    with `field: reason` on stderr."""
    if base and not branch:
        _refuse_arm("worktree.base",
                    "--base names the ref a new --branch forks from; "
                    "pass --branch too")
    payload = {key: value for key, value in (
        ("cwd", cwd), ("provider", provider), ("model", model),
        ("launch", launch), ("permission_mode", permission_mode),
        ("effort", effort)) if value}
    if branch:
        payload["worktree"] = {"branch": branch, "base": base or ""}
    if skill:
        payload["skill"] = {"name": skill}
    try:
        result = _arm_via_server(task_id, payload)
    except ConnectionRefusedError:
        result = _arm_here(task_id, payload)
    _emit(result, json, _render_arm)


def _refuse_arm(field: Optional[str], reason: str) -> None:
    typer.echo(f"{field or 'arm'}: {reason}", err=True)
    raise typer.Exit(code=1)


def _arm_via_server(task_id: int, payload: dict) -> dict:
    """The server's arm route, under a token signed here.

    The route is editor-only and this process holds no login, but it reads the
    same install secret the server signs with — whoever can read that already
    owns the DB, so the token admits nothing new. Unlike a loopback bypass it
    does not open the route to every request a same-host proxy forwards.
    """
    from lib.auth import create_token
    from lib.settings import settings

    base = settings.agent_memory.inject_server_url.rstrip("/")
    request = urllib.request.Request(
        f"{base}/api/tasks/{task_id}/arm", data=_json.dumps(payload).encode(),
        method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + create_token(0, "regin-cli", "editor"),
        })
    try:
        with _open_loopback(request, timeout=30) as response:
            return _json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            body = _json.loads(exc.read() or b"{}")
        except ValueError:
            body = {}
        _refuse_arm(body.get("field"),
                    body.get("error") or f"server refused ({exc.code})")
    except OSError as exc:
        if _refused_connection(exc):
            raise ConnectionRefusedError from exc
        _refuse_arm(None, f"cannot reach regin at {base}: {exc}")


def _arm_here(task_id: int, payload: dict) -> dict:
    """Arm in this process when no server is up. Nothing is launched from
    here — an SDK child would die with this CLI — so a task already free to
    run waits for the server."""
    from lib.tasks import arm, runs
    from lib.tasks.capture import CaptureRejected

    try:
        fields = arm.parse_arm(payload)
        arm.check_target(fields)
        task = store.arm_task(task_id, **fields)
    except CaptureRejected as exc:
        _refuse_arm(exc.field, str(exc))
    except store.TransitionRejected as exc:
        _refuse_arm("status", str(exc))
    if task is None:
        _refuse_arm("task_id", f"no task #{task_id}")
    typer.echo("task arm: regin is not running — armed here", err=True)
    return {"task": task, "armed": runs.armed_attempt(task_id)}


def _render_arm(result: dict) -> None:
    task = result["task"]
    params = (result.get("armed") or {}).get("params") or {}
    print(f"#{task['id']} {task['status']}: {task['title']}")
    for key in ("cwd", "provider", "launch", "worktree", "skill"):
        if params.get(key):
            print(f"{key + ':':<10} {_json.dumps(params[key])}")


def _open_loopback(request, timeout: float):
    from lib.tasks.finish_report import open_loopback
    return open_loopback(request, timeout)


def _refused_connection(exc: OSError) -> bool:
    from lib.tasks.finish_report import refused_connection
    return refused_connection(exc)


def _render_finish(result: dict) -> None:
    if not result.get("settled"):
        print(f"run #{result['run_id']} was already settled — nothing to do")
        return
    fired = result.get("fired") or []
    print(f"run #{result['run_id']} settled; task #{result['task_id']} closed")
    print(f"released: {', '.join(f'#{t}' for t in fired) if fired else '—'}")


@tasks_app.command(
    "commands",
    help="Show this project's command table, or a draft when it has none.",
)
def cmd_commands(
    repo_root: str = typer.Option(".", "--repo", help="Project root to read."),
):
    root = os.path.abspath(repo_root)
    try:
        commands = load_project_commands(root)
    except CommandsAbsent as absent:
        # Its own exit code: both paths print JSON, and a caller must be able
        # to tell a contract the project made from a guess nobody has agreed to.
        print_draft(absent.draft, absent.path, sys.stdout)
        raise typer.Exit(code=DRAFT_EXIT)
    except CommandsRejected as rejected:
        typer.echo(f"task commands: {rejected}", err=True)
        raise typer.Exit(code=1)
    print(_json.dumps(commands.to_json_obj(), indent=2, ensure_ascii=False))


@tasks_app.command(
    "slice-check",
    help="Refuse or accept a feature's spec as sliceable, before the table "
         "is written back.",
)
def cmd_slice_check(
    feature: int = typer.Option(..., "--feature", help="Feature id to read."),
):
    """The first pass's gate, runnable on its own.

    Exit 1 is a refusal with its reasons on stderr — a slicing session that
    cannot see *why* a spec was refused rewrites the table and is refused
    again.
    """
    from lib.features import store as feature_store  # noqa: PLC0415
    from lib.tasks.slice_table import SliceTableError  # noqa: PLC0415
    from lib.tasks.slicing import SlicingRefusal, check_slicing  # noqa: PLC0415

    try:
        row = feature_store.require(feature)
    except feature_store.FeatureError as exc:
        typer.echo(f"task slice-check: {exc}", err=True)
        raise typer.Exit(code=2)
    try:
        rows = check_slicing(row.body or "")
    except SlicingRefusal as refusal:
        typer.echo(f"task slice-check: {refusal}", err=True)
        if not refusal.problems:
            for reason in refusal.missing:
                typer.echo(f"  - {reason}", err=True)
        raise typer.Exit(code=1)
    except SliceTableError as broken:
        typer.echo(f"task slice-check: {broken}", err=True)
        raise typer.Exit(code=1)
    print(_json.dumps({"feature": feature,
                       "slices": [r.n for r in rows]},
                      indent=2, ensure_ascii=False))


@tasks_app.command(
    "transcribe",
    help="Turn a feature spec's slice table into tasks and dependency edges.",
)
def cmd_transcribe(
    feature_id: int = typer.Argument(..., help="The feature whose spec holds "
                                     "the slice table."),
    branch_prefix: str = typer.Option(None, "--branch-prefix",
                                      help="Branch stem for the proposed arm "
                                      "parameters; defaults to f<ID>/."),
    as_json: bool = typer.Option(False, "--json", help="Emit the raw result."),
):
    from lib.features import store as feature_store
    from lib.tasks.feature_slices import SlicesRefused, transcribe_feature
    from lib.tasks.transcribe import TranscribeRefused

    try:
        result = transcribe_feature(feature_id, branch_prefix=branch_prefix)
    except feature_store.FeatureError:
        typer.echo(f"task transcribe: no feature #{feature_id}", err=True)
        raise typer.Exit(code=1)
    except SlicesRefused as refused:
        typer.echo(f"task transcribe: {refused}", err=True)
        raise typer.Exit(code=1)
    except TranscribeRefused as refused:
        typer.echo(f"task transcribe: refused — {refused}", err=True)
        raise typer.Exit(code=1)
    _emit(result.to_json_obj(), as_json, _render_transcription)
    if result.refused:
        raise typer.Exit(code=REFUSED_SLICE_EXIT)


def _render_transcription(payload: dict) -> None:
    print(f"feature #{payload['feature_id']}: "
          f"{len(payload['slices'])} slice(s)")
    for entry in payload["slices"]:
        task = f"#{entry['task_id']}" if entry["task_id"] else "—"
        line = f"  {entry['n']:<4} {entry['action']:<9} {task}"
        if entry["deps"]:
            line += "  after " + ", ".join(f"#{d}" for d in entry["deps"])
        print(line)
        if entry["detail"]:
            print(f"       {entry['detail']}")
        if entry["arm"]:
            worktree = entry["arm"]["worktree"]
            print(f"       arm: {worktree['branch']} off {worktree['base']} "
                  f"· {entry['arm']['skill']['name']}")


@tasks_app.command(
    "tail",
    help="The three chain-tail slice rows (integrate → migrate → verify), "
         "rendered from this project's command table for the first pass to "
         "paste into the spec.",
)
def cmd_tail(
    feature: int = typer.Option(..., "--feature", help="Feature id to read."),
    repo_root: str = typer.Option(".", "--repo",
                                  help="Project root holding the command table."),
    base: Optional[str] = typer.Option(
        None, "--base", help="Integration branch the tail runs on; "
                             "defaults to f<ID>/integration."),
):
    """Print the rows; writing them back is the slicing session's act.

    The tail is the first pass's output, so this prints markdown a human
    reviews rather than editing `features.body` itself — the spec's
    revision history is the approval record, and a command that edited the
    spec would put rows into it nobody read.
    """
    from lib.features import store as feature_store  # noqa: PLC0415
    from lib.tasks.slice_table import SliceTableError, parse_slice_table  # noqa: PLC0415
    from lib.tasks.slicing import render_slice_table  # noqa: PLC0415
    from lib.tasks.tail import tail_slice_rows  # noqa: PLC0415

    root = os.path.abspath(repo_root)
    try:
        row = feature_store.require(feature)
    except feature_store.FeatureError as exc:
        typer.echo(f"task tail: {exc}", err=True)
        raise typer.Exit(code=2)
    try:
        commands = load_project_commands(root)
    except CommandsAbsent as absent:
        typer.echo(f"task tail: no command table at {absent.path} — every "
                   "command in the tail is read from it. Run `regin task "
                   "commands` for a draft, agree it, then re-run.", err=True)
        raise typer.Exit(code=1)
    except CommandsRejected as rejected:
        typer.echo(f"task tail: {rejected}", err=True)
        raise typer.Exit(code=1)
    try:
        rows = parse_slice_table(row.body or "")
    except SliceTableError as broken:
        typer.echo(f"task tail: {broken}", err=True)
        raise typer.Exit(code=1)
    tail_base = base or f"f{feature}/integration"
    tail = tail_slice_rows(rows, commands, tail_base)
    print(render_slice_table(tail, tail_base))


from cli.commands import task_schedule as _task_schedule  # noqa: E402

_task_schedule.register(tasks_app)
