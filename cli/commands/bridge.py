"""`regin bridge listen` — a session's end of the agent bridge's Mod channel.

The regin-bridge mod spawns this once per session. It holds
`GET /api/bridge/listen` open with the bridge token and prints one JSON line
per frame delivered to that session: a prompt, whose `text` the mod submits,
or a control `op` such as `abort`.
While it runs, `deliver()` prefers it over the session's tmux pane.

`regin bridge hold` / `release` are the regin-permit mod's side of a held
permission request (`lib/agent_bridge/mod_hold.py`): hold reads a HoldRequest
on stdin, keeps `GET /api/bridge/hold` open, and prints the one HoldOutcome the
web sends; its exit code says which ending it got.
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
    with _client(url) as client:
        with client.stream("GET", "/api/bridge/listen",
                           params={"session_id": session}) as resp:
            if resp.status_code != 200:
                print(f"bridge listen refused: HTTP {resp.status_code}",
                      file=sys.stderr)
                raise typer.Exit(1)
            for line in listener_lines(resp.iter_lines()):
                print(line, flush=True)


# The mod's contract: 0 = decided (stdout holds the outcome), 3 = released to
# the terminal. Anything else sends it back to the native dialog.
EXIT_DECIDED = 0
EXIT_UNREACHABLE = 1
EXIT_RELEASED = 3
EXIT_STREAM_ENDED = 4  # the hold's stream closed with no ending: it is gone


def _client(url: Optional[str]) -> httpx.Client:
    base = url or f"http://127.0.0.1:{settings.web_port}"
    headers = {"Authorization": f"Bearer {settings.agent_bridge.token}"}
    # trust_env=False: an inherited HTTP(S)_PROXY must not carry a loopback
    # stream, nor see the bridge token.
    return httpx.Client(base_url=base, headers=headers, timeout=None,
                        trust_env=False)


def parse_hold_request(raw: str) -> dict | None:
    """The HoldRequest `{tool, input, reason?}` on stdin, or None when malformed."""
    try:
        req = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(req, dict):
        return None
    tool = req.get("tool")
    if not isinstance(tool, str) or not tool:
        return None
    return req


def hold_ending(lines: Iterable[str]) -> tuple[int, str | None]:
    """(exit code, stdout line) for the frames after the stream's ready line."""
    for line in lines:
        if not line.strip():
            continue
        frame = json.loads(line)
        kind = frame.get("type")
        if kind == "outcome":
            outcome = {k: frame[k] for k in ("op", "behavior", "reason")
                       if k in frame}
            return EXIT_DECIDED, json.dumps(outcome)
        if kind == "released":
            return EXIT_RELEASED, None
    return EXIT_STREAM_ENDED, None


def _post_card(client: httpx.Client, session: str, tool_use_id: str,
               req: dict) -> None:
    """Write the held call's PENDING card; a lost card never blocks the hold."""
    from lib.agent_bridge.mod_hold import card_span
    from lib.hook_plugin import build_span

    span = build_span(**card_span(session, tool_use_id, req["tool"],
                                  req.get("input")))
    try:
        client.post("/api/session-spans", json=span, timeout=5)
    except httpx.HTTPError as exc:
        print(f"bridge hold: card not written: {exc}", file=sys.stderr)


def _hold(client: httpx.Client, session: str, tool_use_id: str,
          req: dict) -> tuple[int, str | None]:
    _post_card(client, session, tool_use_id, req)
    params = {"session_id": session, "tool_use_id": tool_use_id}
    with client.stream("GET", "/api/bridge/hold", params=params) as resp:
        if resp.status_code != 200:
            print(f"bridge hold refused: HTTP {resp.status_code}",
                  file=sys.stderr)
            return EXIT_UNREACHABLE, None
        lines = resp.iter_lines()
        next(lines, None)  # the ready line
        return hold_ending(lines)


@bridge_app.command("hold", help="Hold a permission request open until the web decides it.")
def cmd_hold(
    session: str = typer.Option(..., "--session", help="The session (trace) id."),
    tool_use_id: str = typer.Option(..., "--tool-use-id", help="The gated call's tool_use_id."),
    url: Optional[str] = typer.Option(
        None, "--url", help="regin server base URL (default: the local server)."),
) -> None:
    req = parse_hold_request(sys.stdin.read())
    if req is None:
        print("bridge hold: stdin is not a HoldRequest {tool, input}",
              file=sys.stderr)
        raise typer.Exit(EXIT_UNREACHABLE)
    try:
        with _client(url) as client:
            code, line = _hold(client, session, tool_use_id, req)
    except httpx.ConnectError as exc:
        print(f"bridge hold: {exc}", file=sys.stderr)
        raise typer.Exit(EXIT_UNREACHABLE)
    except httpx.HTTPError as exc:
        print(f"bridge hold: {exc}", file=sys.stderr)
        raise typer.Exit(EXIT_STREAM_ENDED)
    if line is not None:
        print(line, flush=True)
    raise typer.Exit(code)


@bridge_app.command("release", help="Hand a held permission request back to the terminal.")
def cmd_release(
    session: str = typer.Option(..., "--session", help="The session (trace) id."),
    tool_use_id: str = typer.Option(..., "--tool-use-id", help="The gated call's tool_use_id."),
    url: Optional[str] = typer.Option(
        None, "--url", help="regin server base URL (default: the local server)."),
) -> None:
    try:
        with _client(url) as client:
            resp = client.post("/api/bridge/release", timeout=10,
                               json={"session_id": session,
                                     "tool_use_id": tool_use_id})
    except httpx.HTTPError as exc:
        print(f"bridge release: {exc}", file=sys.stderr)
        raise typer.Exit(1)
    released = resp.status_code == 200 and resp.json().get("released") is True
    raise typer.Exit(0 if released else 1)


def parse_phase_event(raw: str) -> dict | None:
    """The `{event, turn_id?, reason?}` on stdin, or None when malformed."""
    try:
        ev = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(ev, dict) or not isinstance(ev.get("event"), str):
        return None
    return {k: ev[k] for k in ("event", "turn_id", "reason") if k in ev}


@bridge_app.command("phase", help="Report one main-loop turn event (JSON on stdin) to the session's phase.")
def cmd_phase(
    session: str = typer.Option(..., "--session", help="The session (trace) id."),
    url: Optional[str] = typer.Option(
        None, "--url", help="regin server base URL (default: the local server)."),
) -> None:
    ev = parse_phase_event(sys.stdin.read())
    if ev is None:
        print("bridge phase: stdin is not a turn event {event}", file=sys.stderr)
        raise typer.Exit(1)
    try:
        with _client(url) as client:
            resp = client.post("/api/bridge/phase", timeout=5,
                               json={"session_id": session, **ev})
    except httpx.HTTPError as exc:
        print(f"bridge phase: {exc}", file=sys.stderr)
        raise typer.Exit(1)
    if resp.status_code != 200:
        print(f"bridge phase refused: HTTP {resp.status_code}", file=sys.stderr)
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    app.add_typer(bridge_app)
