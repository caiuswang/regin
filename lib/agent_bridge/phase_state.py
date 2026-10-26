"""A session's main-loop phase, driven by the turn events its regin-bridge mod reports.

| state      | event                 | next       |
|------------|-----------------------|------------|
| any        | turn.start(t)         | thinking/t |
| any        | turn.step(t)          | thinking/t |
| any        | tool.call(t)          | tool/t     |
| turn == t  | turn.complete(t)      | idle       |
| turn != t  | turn.complete(t)      | unchanged  |

Nothing here reads a clock: a phase holds until the next event moves it. It is
only believed while the session's mod listener is connected (`current`), since
a mod that went away can no longer report the turn ending.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from lib.agent_bridge import mod_channel

THINKING = "thinking"
TOOL = "tool"
IDLE = "idle"

EVENTS = ("turn.start", "turn.step", "tool.call", "turn.complete")

_ENTERS = {"turn.start": THINKING, "turn.step": THINKING, "tool.call": TOOL}


@dataclass(frozen=True)
class Phase:
    phase: str
    turn_id: str | None
    reason: str | None = None


_lock = threading.Lock()
_phases: dict[str, Phase] = {}


def transition(state: Phase | None, event: str, turn_id: str | None,
               reason: str | None = None) -> Phase | None:
    if event in _ENTERS:
        return Phase(_ENTERS[event], turn_id)
    if event == "turn.complete":
        if state is not None and state.turn_id not in (None, turn_id):
            return state
        return Phase(IDLE, None, reason)
    raise ValueError(f"unknown turn event: {event}")


def record(session_id: str, event: str, turn_id: str | None,
           reason: str | None = None) -> Phase:
    """Apply the event; kept only while a listener is connected, since
    `current` would never read it otherwise and nothing else frees it."""
    listening = mod_channel.has_listener(session_id)
    with _lock:
        new = transition(_phases.get(session_id), event, turn_id, reason)
        if listening:
            _phases[session_id] = new
    return new


def current(session_id: str) -> Phase | None:
    """The reported phase, or None when no mod is listening for the session."""
    if not mod_channel.has_listener(session_id):
        return None
    with _lock:
        return _phases.get(session_id)


def forget(session_id: str) -> None:
    with _lock:
        _phases.pop(session_id, None)
