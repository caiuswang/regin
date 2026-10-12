"""Held permission requests: a session's regin-permit mod waiting on the web.

A held request is one open `GET /api/bridge/hold` stream, which the mod's
`regin bridge hold` child keeps open while its `tool.check` hook is parked. It
is registered under `(session_id, tool_use_id)` while that stream's generator
runs and leaves the registry exactly once, by whichever ending comes first:

    held → resolved   a web decision named it (the stream writes the outcome)
    held → released   the terminal took it back (`regin bridge release`)
    held → gone       the hold process died, or the server stopped (EOF)

Leaving `held` is one pop under the lock, so two endings can never both win,
and a decision that loses to another ending is told so (`resolve` → False).
The end of the connection is noticed the way `mod_channel` notices it: `select`
on the client socket and a wake pair, never a heartbeat or a timeout.
"""

from __future__ import annotations

import json
import select
import socket
import threading
from dataclasses import dataclass
from typing import Iterator

from lib.activity_log import get_activity_logger
from lib.agent_bridge.mod_channel import _peer_alive

log = get_activity_logger("agent_bridge")

RESOLVED = "resolved"
RELEASED = "released"
GONE = "gone"

_lock = threading.Lock()
_held: dict[tuple[str, str], "Hold"] = {}


class AlreadyHeld(Exception):
    """A second stream tried to hold a `(session_id, tool_use_id)` already held."""


class Hold:
    def __init__(self, session_id: str, tool_use_id: str):
        self.session_id = session_id
        self.tool_use_id = tool_use_id
        self.ending: str | None = None
        self.frame: dict | None = None
        self._wake_r, self._wake_w = socket.socketpair()

    @property
    def key(self) -> tuple[str, str]:
        return (self.session_id, self.tool_use_id)

    def _end(self, ending: str, frame: dict | None) -> None:
        self.ending = ending
        self.frame = frame
        self._wake_w.send(b"\0")

    def wait(self, peer: socket.socket | None) -> dict | None:
        """Block until an ending is set (its frame) or the peer hung up (None)."""
        watched = [self._wake_r] if peer is None else [self._wake_r, peer]
        while True:
            readable, _, _ = select.select(watched, [], [])
            if self._wake_r in readable:
                return self.frame
            if not _peer_alive(peer):
                return None

    def close(self) -> None:
        self._wake_r.close()
        self._wake_w.close()


def _register(hold: Hold) -> None:
    with _lock:
        if hold.key in _held:
            raise AlreadyHeld(hold.key)
        _held[hold.key] = hold
    log.write("bridge_hold_registered", trace_id=hold.session_id,
              tool_use_id=hold.tool_use_id)


def _end_held(session_id: str, tool_use_id: str, ending: str,
              frame: dict) -> bool:
    # Pop and wake under one lock: the stream's `_finish` takes the same lock
    # before closing the wake pair, so a frame is never sent into a closed one.
    with _lock:
        hold = _held.pop((session_id, tool_use_id), None)
        if hold is None:
            return False
        hold._end(ending, frame)
    return True


def _finish(hold: Hold) -> None:
    """The stream's own end: a hold nobody else ended is gone."""
    with _lock:
        mine = _held.get(hold.key) is hold
        if mine:
            del _held[hold.key]
            hold.ending = GONE
    hold.close()
    if hold.ending is None:
        return  # never registered
    log.write("bridge_hold_ended", trace_id=hold.session_id,
              tool_use_id=hold.tool_use_id, ending=hold.ending)


class HoldBody:
    """The hold response body: a ready line, then at most one ending line.

    Registers on its first step, not on construction: a body the server
    discards before streaming it is never closed, and a registration made
    earlier would stand forever. Has its own `close` because the server closes
    a started body, and a generator closed mid-yield must still deregister.
    A key taken between the route's check and the first step ends the stream
    with a `refused` line, which the hold child reads as no ending at all.
    """

    def __init__(self, session_id: str, tool_use_id: str,
                 peer: socket.socket | None):
        self._hold = Hold(session_id, tool_use_id)
        self._peer = peer
        self._closed = False

    def __iter__(self) -> Iterator[str]:
        hold = self._hold
        try:
            _register(hold)
        except AlreadyHeld:
            yield _line({"type": "refused", "reason": "already held"})
            return
        yield _line({"type": "ready", "session_id": hold.session_id,
                     "tool_use_id": hold.tool_use_id})
        frame = hold.wait(self._peer)
        if frame is not None:
            yield _line(frame)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            _finish(self._hold)


def _line(frame: dict) -> str:
    return json.dumps(frame) + "\n"


def held_ids(session_id: str) -> list[str]:
    """The tool_use_ids held for `session_id`, oldest first."""
    with _lock:
        return [tu for (sid, tu) in _held if sid == session_id]


def is_held(session_id: str, tool_use_id: str) -> bool:
    with _lock:
        return (session_id, tool_use_id) in _held


@dataclass(frozen=True)
class Match:
    """Which held request a web decision for a session is aimed at.

    `tool_use_id` is set on a hit; `ambiguous` when none was named and the
    session holds several; both empty when nothing is held for it.
    """
    tool_use_id: str | None = None
    ambiguous: bool = False


def match(session_id: str, tool_use_id: str | None) -> Match:
    if tool_use_id:
        return Match(tool_use_id if is_held(session_id, tool_use_id) else None)
    held = held_ids(session_id)
    if len(held) == 1:
        return Match(held[0])
    return Match(ambiguous=len(held) > 1)


def resolve(session_id: str, tool_use_id: str, behavior: str,
            reason: str = "") -> bool:
    """End the held request with a decision; False when it is no longer held."""
    outcome = {"type": "outcome", "op": "decide", "behavior": behavior}
    if reason:
        outcome["reason"] = reason
    if not _end_held(session_id, tool_use_id, RESOLVED, outcome):
        return False
    log.write("bridge_delivery_outcome", trace_id=session_id, delivered=True,
              detail="decided over the held stream", channel="mod",
              tool_use_id=tool_use_id, behavior=behavior)
    return True


def release(session_id: str, tool_use_id: str) -> bool:
    """Give the request back to the terminal; False when it is no longer held."""
    if not _end_held(session_id, tool_use_id, RELEASED, {"type": "released"}):
        return False
    log.write("bridge_hold_released", trace_id=session_id,
              tool_use_id=tool_use_id)
    return True


def card_span(session_id: str, tool_use_id: str, tool: str,
              tool_input) -> dict:
    """The PENDING card a hold writes for the call it parks.

    The same row PreToolUse writes for that call (`pending-<tool_use_id>`, the
    shared `ToolCall` projection, the `gate_key` of tool + input), so a call
    both of them saw upserts into one card, and a later classic
    `PermissionRequest` — which carries no `tool_use_id` — pairs onto it by
    `gate_key` in the serve-time merge.
    """
    from lib.agent_events import ToolCall, to_span
    from lib.trace.pending_spans import gate_key

    ti = tool_input if isinstance(tool_input, dict) else {}
    span = to_span(ToolCall(trace_id=session_id, tool_name=tool,
                            tool_use_id=tool_use_id, tool_input=ti))
    key = gate_key(tool, tool_input)
    if key:
        span["attributes"]["gate_key"] = key
    span["attributes"]["mod_held"] = True
    return span
