"""`regin bridge listen` — a session's end of the agent bridge's Mod channel.

The regin-bridge mod spawns this once per session. It holds
`GET /api/bridge/listen` open with the bridge token and prints one JSON line
per frame delivered to that session: a prompt, whose `text` the mod submits,
or a control `op` such as `abort`.
While it runs, `deliver()` prefers it over the session's tmux pane.
"""

from __future__ import annotations

import json
import sys
from typing import Iterable, Iterator, Optional

import httpx
import typer

from lib.settings import settings

bridge_app = typer.Typer(
    name="bridge",
    help="Agent bridge: a session's Mod channel",
    no_args_is_help=True,
)


_FIELDS = {"prompt": ("text", "id", "session_id"),
           "control": ("op", "id", "session_id")}


def listener_lines(lines: Iterable[str]) -> Iterator[str]:
    """The stream's prompt and control frames as the JSON lines the mod reads."""
    for line in lines:
        if not line.strip():
            continue
        frame = json.loads(line)
        fields = _FIELDS.get(frame.get("type"))
        if fields is None:
            continue
        yield json.dumps({k: frame[k] for k in fields})


@bridge_app.command("listen", help="Print each prompt delivered to a session, one JSON line each.")
def cmd_listen(
    session: str = typer.Option(..., "--session", help="The session (trace) id to listen for."),
    url: Optional[str] = typer.Option(
        None, "--url", help="regin server base URL (default: the local server)."),
) -> None:
    base = url or f"http://127.0.0.1:{settings.web_port}"
    headers = {"Authorization": f"Bearer {settings.agent_bridge.token}"}
    # trust_env=False: an inherited HTTP(S)_PROXY must not carry a loopback
    # stream, nor see the bridge token.
    with httpx.Client(base_url=base, headers=headers, timeout=None,
                      trust_env=False) as client:
        with client.stream("GET", "/api/bridge/listen",
                           params={"session_id": session}) as resp:
            if resp.status_code != 200:
                print(f"bridge listen refused: HTTP {resp.status_code}",
                      file=sys.stderr)
                raise typer.Exit(1)
            for line in listener_lines(resp.iter_lines()):
                print(line, flush=True)


def register(app: typer.Typer) -> None:
    app.add_typer(bridge_app)
