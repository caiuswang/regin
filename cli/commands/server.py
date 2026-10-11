"""`regin serve` — start the Flask dashboard."""

from __future__ import annotations

import typer

from lib.settings import settings


def _start_workflow_watcher(debug: bool) -> None:
    """Start the dynamic-workflow trace watcher in a daemon thread.

    Captures Claude Code workflow runs (run -> phase -> agent -> turn) into
    the trace DB while the dashboard is up. Under the Werkzeug reloader
    (``--debug``) only the reloaded child process runs it, so the watcher
    isn't started twice.
    """
    import os
    import threading

    if not settings.workflow_watch:
        return
    if debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    from lib.trace.workflow_ingest import watch

    threading.Thread(
        target=watch, kwargs={"poll_seconds": 5.0},
        name="workflow-watcher", daemon=True,
    ).start()
    print("  (capturing dynamic-workflow runs into the trace dashboard)")


def _start_pending_reaper(debug: bool) -> None:
    """Start the opt-in superseded-pending-span reaper in a daemon thread.

    Gated on ``settings.trace_retention.auto_reap`` (off by default), so the
    append-only span store can self-prune the placeholder rows merge already
    hides. Mirrors the workflow watcher: under the Werkzeug reloader
    (``--debug``) only the reloaded child runs it, so it isn't started twice.
    """
    import os
    import threading

    cfg = settings.trace_retention
    if not cfg.auto_reap:
        return
    if debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    from lib.trace.reap import reap_loop

    threading.Thread(
        target=reap_loop,
        kwargs={"interval_hours": cfg.interval_hours,
                "idle_minutes": cfg.idle_minutes},
        name="pending-reaper", daemon=True,
    ).start()
    print("  (auto-reaping superseded pending spans every "
          f"{cfg.interval_hours}h)")


def _warn_if_schema_behind() -> None:
    """Booting never migrates, so a DB behind head only surfaces later as a
    500 on whichever page reads a changed table — say so at startup instead."""
    from lib.doctor import _schema_revision_items

    for item in _schema_revision_items():
        if not item["present"]:
            typer.secho(f"WARNING: database schema is {item['version']} — "
                        "run `regin migrate` before using the dashboard",
                        fg=typer.colors.YELLOW, err=True)


def cmd_serve(
    host: str = typer.Option(
        "127.0.0.1", "--host",
        help="Bind address (default: 127.0.0.1, localhost-only; "
             "use 0.0.0.0 to expose on the network)",
    ),
    port: int = typer.Option(settings.web_port, "--port"),
    debug: bool = typer.Option(False, "--debug"),
    replica: bool = typer.Option(
        False, "--replica",
        help="Read-only replica for a copy of the live DB: skip the boot "
             "reap, workflow watcher and pending reaper; refuse /api writes "
             "except login",
    ),
) -> None:
    from web.app import create_app
    _warn_if_schema_behind()
    server = create_app(replica=replica)
    display_host = 'localhost' if host in ('127.0.0.1', '0.0.0.0') else host
    print(f"Starting dashboard at http://{display_host}:{port}")
    if host == '0.0.0.0':
        print("  (bound to 0.0.0.0 — dashboard is reachable from the network)")
    if replica:
        print("  (replica — boot reap and background writers off, /api writes refused)")
    else:
        _start_workflow_watcher(debug)
        _start_pending_reaper(debug)
    server.run(host=host, port=port, debug=debug)


def register(app: typer.Typer) -> None:
    app.command("serve", help="Start web dashboard")(cmd_serve)
