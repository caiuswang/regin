"""`regin serve` — start the Flask dashboard."""

from __future__ import annotations

import typer

from lib.settings import settings


def cmd_serve(
    host: str = typer.Option(
        "127.0.0.1", "--host",
        help="Bind address (default: 127.0.0.1, localhost-only; "
             "use 0.0.0.0 to expose on the network)",
    ),
    port: int = typer.Option(settings.web_port, "--port"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    from web.app import create_app
    server = create_app()
    display_host = 'localhost' if host in ('127.0.0.1', '0.0.0.0') else host
    print(f"Starting dashboard at http://{display_host}:{port}")
    if host == '0.0.0.0':
        print("  (bound to 0.0.0.0 — dashboard is reachable from the network)")
    server.run(host=host, port=port, debug=debug)


def register(app: typer.Typer) -> None:
    app.command("serve", help="Start web dashboard")(cmd_serve)
