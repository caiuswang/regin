"""Process-local registry of live ACP runs (feature #141).

`lib.agent_sdk.registry` is the ownership layer the session page and the
steer routes ask "is this trace id regin's to drive?" — and before this
module the ACP channel arm had no answer there, so a live kimi run read as
"not steerable" and a send had nowhere to go. This registry is that answer
for channel-launched runs: `launch_run` registers the handle before the turn
thread starts and unregisters it in `close`, so the whole live window is
owned.

Scope is one process, same reasoning as the SDK registry: a channel child
only lives as long as the process that spawned it. `agent_sdk.registry`
delegates to this module lazily at call time — the dependency must never
run the other way at import time (`agent_acp` never imports `agent_sdk` at
module level), or the two registry modules form an import cycle.

`_retired` exists for the refusal path, not for liveness: `unregister`
forgets a run entirely, and a steer arriving later would fall through to
the SDK arm and be told "no live agent session" — the wrong verdict for a
session regin launched and closed. The id is kept in a small set so the
steer can be answered "no longer running" instead. It only grows, but an
entry is one string per channel run ever launched in this process.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_handles: dict[str, object] = {}
_retired: set[str] = set()


def register(handle) -> None:
    with _lock:
        _handles[handle.trace_id] = handle
        _retired.discard(handle.trace_id)


def unregister(trace_id: str) -> None:
    with _lock:
        if _handles.pop(trace_id, None) is not None:
            _retired.add(trace_id)


def is_owned(trace_id: str) -> bool:
    with _lock:
        return trace_id in _handles


def get(trace_id: str):
    """The live handle for `trace_id`, or None when this process holds none."""
    with _lock:
        return _handles.get(trace_id)


def handles() -> list:
    """Every live handle this process holds."""
    with _lock:
        return list(_handles.values())


def open_tool_use_ids(trace_ids) -> set[str]:
    """tool_use_ids a live run here still has in flight, across `trace_ids`
    (an alias group). Empty for an id no live run owns."""
    with _lock:
        held = [_handles[t] for t in trace_ids if t in _handles]
    ids: set[str] = set()
    for handle in held:
        ids |= handle.open_tool_use_ids()
    return ids


def was_owned(trace_id: str) -> bool:
    """Whether a channel run here ever owned `trace_id`, live or closed."""
    with _lock:
        return trace_id in _retired
