"""What a live ACP run is doing right now, read off its `session/update` stream.

The trace only learns about a kimi run through hooks and transcript tails,
which go silent for the whole of a long tool call, a long think, or a call
parked on something no one on the wire can answer (an MCP OAuth login). The
ACP wire is not silent: every tool call is announced with a human title and
walks pending → in_progress → completed/failed, thoughts and replies stream
as chunks, and `usage_update` reports the context fill. This tracker folds
that stream into the `ScreenState` shape the status surfaces already render
for terminal panes and SDK runs.
"""

from __future__ import annotations

import threading
import time

_CLOSED_TOOL_STATUSES = frozenset({"completed", "failed"})
_STREAM_VERBS = {
    "agent_thought_chunk": "Thinking",
    "agent_message_chunk": "Responding",
}


def hook_tool_use_id(call_id: str) -> str:
    prefix, sep, rest = call_id.partition(":")
    return rest if sep and prefix.isdigit() else call_id


class AcpActivity:
    """Thread-safe: the client's reader thread applies updates, a Flask
    thread reads `screen_state`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turn_started: float | None = None
        # toolCallId → title, insertion-ordered so the newest open call is last.
        self._open_tools: dict[str, str] = {}
        self._stream_verb: str | None = None
        self._ctx_pct: int | None = None

    def turn_started(self) -> None:
        with self._lock:
            self._turn_started = time.monotonic()
            self._open_tools.clear()
            self._stream_verb = None

    def turn_ended(self) -> None:
        """A cancelled turn can leave calls announced in_progress forever;
        none of them is running once the prompt has settled."""
        with self._lock:
            self._turn_started = None
            self._open_tools.clear()
            self._stream_verb = None

    def apply(self, update: dict) -> None:
        kind = update.get("sessionUpdate")
        with self._lock:
            if kind in ("tool_call", "tool_call_update"):
                self._apply_tool(update)
            elif kind in _STREAM_VERBS:
                self._stream_verb = _STREAM_VERBS[kind]
            elif kind == "usage_update":
                self._apply_usage(update)

    def _apply_tool(self, update: dict) -> None:
        call_id = update.get("toolCallId")
        if not call_id:
            return
        if update.get("status") in _CLOSED_TOOL_STATUSES:
            self._open_tools.pop(call_id, None)
            return
        title = update.get("title") or self._open_tools.get(call_id) or "tool"
        self._open_tools[call_id] = title
        self._stream_verb = None

    def _apply_usage(self, update: dict) -> None:
        used, size = update.get("used"), update.get("size")
        if isinstance(used, (int, float)) and isinstance(size, (int, float)) \
                and size > 0:
            self._ctx_pct = round(used * 100 / size)

    def open_tool_use_ids(self) -> set[str]:
        """The calls announced and not yet settled, under the id the hooks
        record them by: kimi prefixes its ACP toolCallId with a numeric scope
        (`0:tool_…`), which the hook's tool_use_id does not carry."""
        with self._lock:
            ids = list(self._open_tools)
        return {hook_tool_use_id(call_id) for call_id in ids}

    def screen_state(self, *, session_id: str, cwd: str | None,
                     model: str | None):
        """The run's status now. Not mid-turn reads `busy=False` with no verb:
        an ACP run between turns is parked on its steer queue, which is what
        `idle` means for a run regin owns."""
        from lib.agent_bridge.screen_state import ScreenState
        with self._lock:
            started = self._turn_started
            tool = next(reversed(self._open_tools.values()), None)
            verb = self._stream_verb
            ctx_pct = self._ctx_pct
        busy = started is not None
        if busy and tool is not None:
            verb = "Working"
        return ScreenState(
            busy=busy,
            verb=(verb or "Working") if busy else None,
            elapsed_sec=(max(0, int(time.monotonic() - started))
                         if busy else None),
            tool=tool if busy else None,
            session_id=session_id,
            cwd=cwd,
            model=model or None,
            ctx_pct=ctx_pct,
        )
