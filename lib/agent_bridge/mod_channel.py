"""Mod channel: a session's own regin-bridge mod as a delivery target.

A listener is one held `GET /api/bridge/listen` stream, which the session's
`regin bridge listen` child holds open. It is registered while that stream's
generator runs and unregistered in its `finally`, so a session counts as
listening exactly as long as its connection lives: no heartbeat, no timeout.

The connection's end is noticed without writing to it: the generator sleeps in
`select` on the client socket and a wake pair, and an idle client hanging up
makes its socket readable at EOF. Where no socket is exposed (the Flask test
client) only the wake pair is watched, and the stream ends when it is closed.
"""

from __future__ import annotations

import json
import queue
import select
import socket
import threading
import uuid
from typing import Iterator

from lib.activity_log import get_activity_logger

log = get_activity_logger("agent_bridge")

_lock = threading.Lock()
_listeners: dict[str, list["Listener"]] = {}


class Listener:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._frames: queue.SimpleQueue = queue.SimpleQueue()
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_w.setblocking(False)

    def offer(self, frame: dict) -> None:
        self._frames.put(frame)
        try:
            self._wake_w.send(b"\0")
        except BlockingIOError:
            pass  # wake bytes already pending; the reader drains the queue whole

    def frames(self, peer: socket.socket | None) -> Iterator[dict]:
        while True:
            while not self._frames.empty():
                yield self._frames.get()
            if not self._wait(peer):
                return

    def _wait(self, peer: socket.socket | None) -> bool:
        """Block until a frame is offered (True) or the peer hung up (False)."""
        watched = [self._wake_r] if peer is None else [self._wake_r, peer]
        readable, _, _ = select.select(watched, [], [])
        if peer is not None and peer in readable and not _peer_alive(peer):
            return False
        if self._wake_r in readable:
            self._wake_r.recv(4096)
        return True

    def close(self) -> list[dict]:
        """Release the wake pair; return the frames offered but never streamed."""
        self._wake_r.close()
        self._wake_w.close()
        stranded = []
        while not self._frames.empty():
            stranded.append(self._frames.get())
        return stranded


def _peer_alive(peer: socket.socket) -> bool:
    try:
        data = peer.recv(4096, socket.MSG_PEEK)
    except OSError:
        return False
    if data:
        # The stream's request is fully read, so bytes here are nothing the
        # server answers; discard them or select would report them forever.
        peer.recv(len(data))
    return bool(data)


def _register(listener: Listener) -> None:
    with _lock:
        _listeners.setdefault(listener.session_id, []).append(listener)
    log.write("bridge_mod_listener_registered", trace_id=listener.session_id)


def _unregister(listener: Listener) -> None:
    with _lock:
        held = _listeners.get(listener.session_id, [])
        if listener in held:
            held.remove(listener)
        if not held:
            _listeners.pop(listener.session_id, None)
    # Removal under the lock comes first, so no offer can land after the
    # drain: a frame still queued here was reported delivered and was not.
    stranded = listener.close()
    log.write("bridge_mod_listener_unregistered", trace_id=listener.session_id)
    if stranded:
        log.error("bridge_mod_delivery_lost", trace_id=listener.session_id,
                  frame_ids=[f["id"] for f in stranded])


def ndjson_stream(session_id: str,
                  peer: socket.socket | None) -> Iterator[str]:
    """The listen response body: a ready line, then one line per prompt."""
    listener = Listener(session_id)
    _register(listener)
    try:
        yield _line({"type": "ready", "session_id": session_id})
        for frame in listener.frames(peer):
            yield _line(frame)
    finally:
        _unregister(listener)


def _line(frame: dict) -> str:
    return json.dumps(frame) + "\n"


def offer(session_id: str, text: str) -> bool:
    """Hand `text` to the session's newest listener; False when none is connected.

    The newest wins so a mod that reconnects before its old stream is noticed
    gone does not submit the prompt twice.
    """
    with _lock:
        held = _listeners.get(session_id)
        listener = held[-1] if held else None
        if listener is None:
            return False
        listener.offer({"type": "prompt", "id": uuid.uuid4().hex,
                        "session_id": session_id, "text": text})
    return True
