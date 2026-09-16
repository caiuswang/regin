"""Agent bridge — WebSocket surface: a real terminal on a session's pane.

`xterm.js` in the browser ↔ this socket ↔ a PTY running a grouped tmux client
(`lib/agent_bridge/terminal.py`). Because it *is* a terminal there is no key
allowlist, no key-name mapping, no rate budget and no ANSI-to-HTML step; the
sibling steering routes in `bridge.py` keep all of those and are untouched.

**This is full shell access as the user running `regin serve`.** `tmux
send-keys` into a pane can run anything, and no filter or sanitizer over a
keystroke stream changes that — a partial one would only offer false comfort.
The controls are therefore binary and explicit: `terminal_enabled` is off by
default, and the route demands the **admin** role, one rung above the
`require_editor` that guards message steering.

Auth rides a single-use ticket, not a bearer header: `WebSocket` cannot set
one, exactly as `EventSource` cannot, so this route joins
`PUBLIC_API_ENDPOINTS` with the ticket as its sole gate — the same shape as
`notifications.api_notifications_stream`, reusing the same ticket store. The
ticket names a *user*; the admin check happens here, on redemption.
"""

from __future__ import annotations

import json
import socket
import threading

from flask import Blueprint, request
from flask_sock import Sock

from lib.activity_log import get_activity_logger
from lib.agent_bridge import delivery, store, terminal
from lib.auth import get_user
from lib.notifications import tickets
from lib.settings import settings

terminal_bp = Blueprint('bridge_terminal', __name__)
sock = Sock()
log = get_activity_logger("agent_bridge")

# WebSocket close codes. 1008 is "policy violation" — every refusal here is
# one, and collapsing them onto a single code keeps the client from having to
# distinguish cases it must not reveal anyway (whether a pane exists is not a
# viewer's business).
_REFUSED = 1008
_MAX_DIMENSION = 1000


def _authorize(trace_id: str, ticket: str) -> tuple[dict | None, str | None]:
    """(pane row, None) when this request may open a terminal, else (None, why).

    Every branch resolves before anything is spawned. The pane is resolved
    through `store.get_reachable_pane` and then re-read by
    `delivery._verify_identity`: the first says which registration currently
    owns the pane (a row is keyed by trace_id and never retired, so a terminal
    the operator keeps open accumulates one per session that ever ran there),
    the second says the pane still holds the claude that registered it. A
    recycled pane id must never be attached to.
    """
    if not settings.agent_bridge.terminal_enabled:
        return None, "terminal disabled"
    user_id = tickets.redeem(ticket)
    if user_id is None:
        return None, "invalid ticket"
    user = get_user(user_id)
    if not user or user.get("role") != "admin":
        return None, "admin role required"
    row = store.get_reachable_pane(trace_id)
    if row is None:
        return None, "no reachable pane"
    identity = delivery._verify_identity(row)
    if not identity["ok"]:
        return None, identity["detail"]
    return row, None


def _dimension(raw, fallback: int) -> int:
    """A client-supplied cols/rows value, clamped to something a winsize can
    hold — the browser is not trusted to bound its own geometry."""
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        return fallback
    return min(raw, _MAX_DIMENSION)


def _handle_control(term: terminal.TerminalSession, payload: str) -> None:
    """Apply a text frame: JSON control messages, currently only `resize`.

    Text vs binary is the whole protocol. Raw keystrokes arrive as binary
    frames and go to the PTY untouched, so control messages need no in-band
    escaping and no byte of user input can ever be read as one.
    """
    try:
        msg = json.loads(payload)
    except (ValueError, TypeError):
        return
    if not isinstance(msg, dict) or msg.get("type") != "resize":
        return
    term.resize(_dimension(msg.get("cols"), 80), _dimension(msg.get("rows"), 24))


def _pump_output(ws, term: terminal.TerminalSession) -> None:
    """PTY → socket, until the client exits or the socket goes away."""
    try:
        while True:
            data = term.read()
            if not data:
                break
            ws.send(data)
    except Exception:  # any socket fault ends the stream
        pass
    finally:
        _end_stream(ws)


def _end_stream(ws) -> None:
    """Wake the main thread out of `ws.receive()`, then close.

    `receive()` parks on an event only simple_websocket's own reader thread
    sets, and that thread is blocked in `sock.recv`. Shutting the READ half
    down forces the EOF it turns into that event. Order matters both ways:
    `ws.close()` first would close the socket, after which the shutdown is a
    no-op on a dead descriptor and the wake depends on the platform waking a
    `recv` on a closed fd (Darwin does, Linux does not promise to); and
    `SHUT_RD` rather than `SHUT_RDWR` leaves the write half alive, so the
    close frame that follows still reaches the browser.

    Without the wake the route never leaves `_pump_input`, so its `finally`
    never reaps the PTY — a leaked terminal per browser that went away.
    """
    try:
        ws.sock.shutdown(socket.SHUT_RD)
    except (OSError, AttributeError):
        pass
    try:
        ws.close()
    except Exception:
        pass


def _pump_input(ws, term: terminal.TerminalSession) -> None:
    """Socket → PTY, until the client disconnects."""
    while True:
        data = ws.receive()
        if data is None:
            return
        if isinstance(data, str):
            _handle_control(term, data)
        else:
            term.write(data)


@sock.route('/api/sessions/<trace_id>/terminal', bp=terminal_bp)
def api_session_terminal(ws, trace_id):
    """Full-duplex byte stream between the browser and this session's pane."""
    row, refusal = _authorize(trace_id, request.args.get("ticket") or "")
    if refusal is not None:
        log.write("bridge_terminal_refused", trace_id=trace_id, detail=refusal)
        ws.close(_REFUSED, refusal)
        return
    term = terminal.open_terminal(
        row,
        _dimension(request.args.get("cols", type=int), 80),
        _dimension(request.args.get("rows", type=int), 24),
    )
    if term is None:
        ws.close(_REFUSED, "could not attach")
        return
    log.write("bridge_terminal_session_open", trace_id=trace_id, pid=term.pid)
    # Inside the try from here on: a PTY that outlives its socket is the
    # failure mode, and starting the reader thread can itself fail.
    try:
        threading.Thread(target=_pump_output, args=(ws, term),
                         daemon=True).start()
        _pump_input(ws, term)
    finally:
        term.close()
        log.write("bridge_terminal_session_closed", trace_id=trace_id,
                  pid=term.pid)
