"""`regin task schedule` / `regin task schedules` — F173 scheduled tasks.

Server-only, unlike most task commands: the scheduler thread that acts on a
schedule lives in `regin serve`, and a write must wake it. So these post to
the server's `/api/tasks/<id>/schedule` routes, and a server that is not up
is an error rather than a local write nobody would act on.
"""

from __future__ import annotations

import json as _json
import urllib.error
import urllib.request
from typing import Optional

import typer


def _call(method: str, path: str, payload: Optional[dict] = None):
    from lib.auth import create_token
    from lib.settings import settings
    from lib.tasks.finish_report import open_loopback, refused_connection

    base = settings.agent_memory.inject_server_url.rstrip("/")
    request = urllib.request.Request(
        f"{base}{path}", method=method,
        data=None if payload is None else _json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + create_token(
                     0, "regin-cli", "editor")})
    try:
        with open_loopback(request, timeout=30) as response:
            raw = response.read()
            return _json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        try:
            body = _json.loads(exc.read() or b"{}")
        except ValueError:
            body = {}
        _fail(body.get("error") or f"server refused ({exc.code})")
    except OSError as exc:
        if refused_connection(exc):
            _fail(f"regin is not running at {base} — schedules are written "
                  "and fired by `regin serve`")
        _fail(f"cannot reach regin at {base}: {exc}")


def _fail(reason: str) -> None:
    typer.echo(f"task schedule: {reason}", err=True)
    raise typer.Exit(code=1)


def _payload(at, window, cron, tz) -> Optional[dict]:
    chosen = [flag for flag in (at, window, cron) if flag]
    if len(chosen) > 1:
        _fail("pass only one of --at / --window / --cron")
    if not chosen:
        return None
    body: dict = {"tz": tz} if tz else {}
    if at:
        body.update(kind="at", run_at=at)
    elif window:
        start, sep, end = window.partition("-")
        if not sep:
            _fail("--window needs HH:MM-HH:MM")
        body.update(kind="window", window_start=start, window_end=end)
    else:
        body.update(kind="cron", cron_expr=cron)
    return body


def _render(view: Optional[dict]) -> None:
    if not view:
        print("no schedule")
        return
    rule = {"at": lambda v: f"at {v['run_at']}",
            "window": lambda v: f"window {v['window_start']}-{v['window_end']}",
            "cron": lambda v: f"cron {v['cron_expr']}"}[view["kind"]](view)
    state = "on" if view.get("enabled") else "off"
    print(f"#{view['task_id']} {rule} tz={view['tz']} [{state}]")
    print(f"next:      {view.get('next_fire_at') or '—'}")
    if "gate_open" in view and view["kind"] != "cron":
        print(f"gate:      {'open' if view['gate_open'] else 'shut'}")
    for when in view.get("upcoming") or []:
        print(f"  upcoming {when}")


def cmd_schedule(
    task_id: int = typer.Argument(..., help="Task id."),
    at: Optional[str] = typer.Option(
        None, "--at", help="Gate: not before this ISO time (local to --tz)."),
    window: Optional[str] = typer.Option(
        None, "--window", help="Gate: only start inside HH:MM-HH:MM."),
    cron: Optional[str] = typer.Option(
        None, "--cron", help="Recurring: a new attempt on every 5-field slot."),
    tz: Optional[str] = typer.Option(
        None, "--tz", help="IANA zone; defaults to the server's."),
    off: bool = typer.Option(False, "--off", help="Disable the schedule."),
    on: bool = typer.Option(False, "--on", help="Re-enable the schedule."),
    clear: bool = typer.Option(False, "--clear", help="Delete the schedule."),
    json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    path = f"/api/tasks/{task_id}/schedule"
    body = _payload(at, window, cron, tz)
    if sum(bool(x) for x in (body, off, on, clear)) > 1:
        _fail("pass one action at a time")
    if clear:
        _call("DELETE", path)
        print(f"#{task_id} schedule cleared")
        return
    if off or on:
        view = _call("PUT", path, {"enabled": on})
    elif body is not None:
        view = _call("PUT", path, body)
    else:
        view = _call("GET", path)
    if json:
        print(_json.dumps(view, indent=2))
    else:
        _render(view)


def cmd_schedules(
    json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    rows = _call("GET", "/api/tasks/schedules") or []
    if json:
        print(_json.dumps(rows, indent=2))
        return
    if not rows:
        print("no schedules")
    for row in rows:
        rule = row["run_at"] or (
            f"{row['window_start']}-{row['window_end']}"
            if row["kind"] == "window" else row["cron_expr"])
        state = "on " if row["enabled"] else "off"
        print(f"#{row['task_id']:<5} {state} {row['kind']:<6} {rule:<20} "
              f"next={row['next_fire_at'] or '—'}  {row['task_title'][:50]}")


def register(app: typer.Typer) -> None:
    app.command("schedule", help="Show or set a task's schedule.")(cmd_schedule)
    app.command("schedules", help="List every task schedule.")(cmd_schedules)
