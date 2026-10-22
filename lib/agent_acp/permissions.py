"""Operator decisions for an ACP agent's `session/request_permission`.

A call the agent still needs approval for under its unattended mode
(codebuddy's bypassPermissions keeps a few, `git worktree remove --force`
among them) parks here until the operator answers it from the blocker card —
the same `bridge-decide` → `resolve_permission` route an SDK run's parked
call takes. Answering `cancelled` instead ends the agent's turn, and the run
stops halfway with nobody told why.

A park never expires, for the reason `runner_park._await_operator` gives: a
refusal on a timer is a decision the human did not make. It ends on an
answer, on an interrupt (ACP requires every pending request to be answered
`cancelled` after `session/cancel`), or on the run's close.
"""

from __future__ import annotations

import itertools
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime

from lib.activity_log import get_activity_logger

from .activity import hook_tool_use_id

log = get_activity_logger("agent_acp")

CANCELLED = {"outcome": {"outcome": "cancelled"}}

_ALLOW_KINDS = ("allow_once", "allow")
_REJECT_KINDS = ("reject_once", "reject")


@dataclass
class AcpPark:
    """One `session/request_permission` waiting on the operator."""

    trace_id: str
    tool_use_id: str
    tool_name: str
    tool_input: dict
    options: list
    parked_at: str
    future: Future = field(default_factory=Future)
    kind: str = "tool"


def meta_tool_name(call: dict) -> str:
    """The tool's own name, which ACP leaves to vendor `_meta` (codebuddy:
    `codebuddy.ai/toolName`). `title` is display prose, not a name. The
    serve-time merge needs the name to retire the call's tool placeholder."""
    meta = call.get("_meta") or {}
    return next((str(v) for k, v in meta.items()
                 if k.endswith("/toolName") and v), "")


def selected_outcome(options: list, behavior: str) -> dict:
    """The ACP answer for an allow/deny, picked from the options the agent
    offered: the once-only option of that side, never an `allow_always` the
    operator did not choose. `cancelled` when the agent offered no option for
    that side — the one answer that is neither."""
    wanted = _ALLOW_KINDS if behavior == "allow" else _REJECT_KINDS
    pick = (_first_of_kind(options, lambda kind: kind in wanted)
            or _first_of_kind(options, lambda kind: kind.startswith(wanted[1])))
    if pick is None:
        return CANCELLED
    return {"outcome": {"outcome": "selected", "optionId": pick}}


def _first_of_kind(options: list, accepts) -> str | None:
    ranked = sorted((o for o in options
                     if o.get("optionId") and accepts(str(o.get("kind") or ""))),
                    key=lambda o: o.get("kind") != "allow_once"
                    and o.get("kind") != "reject_once")
    return ranked[0]["optionId"] if ranked else None


def post_resolution(trace_id: str, tool_use_id: str, tool_name: str,
                    behavior: str, detail: str) -> None:
    """Put the resolution on the trace, keyed on the call id: it retires the
    PENDING `permission.request` whichever writer filed it (this module, or
    the agent's own hook)."""
    from lib.agent_events import PermissionResolved
    _post(PermissionResolved(trace_id=trace_id, tool_use_id=tool_use_id,
                             tool_name=tool_name, behavior=behavior,
                             detail=detail))


def _post(event) -> None:
    from lib.agent_events.spans import to_span
    from lib.hook_plugin import post_span
    try:
        post_span(**to_span(event))
    except Exception:  # noqa: BLE001 — the agent must still get its answer
        log.error("acp_permission_span_unrecorded", trace_id=event.trace_id,
                  exc_info=True)


class PermissionParks:
    """The parked permission requests of one ACP run."""

    def __init__(self, trace_id: str) -> None:
        self._trace_id = trace_id
        self._lock = threading.Lock()
        self._parks: dict[int, AcpPark] = {}
        self._ids = itertools.count(1)
        self._closed = False

    def ask(self, params: dict) -> dict:
        """The `session/request_permission` handler. Blocks its caller until
        the operator answers, so it must not run on the client's reader
        thread (`AcpClient.on_request(..., blocking=True)`)."""
        call = params.get("toolCall") or {}
        park = AcpPark(
            trace_id=self._trace_id,
            tool_use_id=hook_tool_use_id(str(call.get("toolCallId") or "")),
            tool_name=meta_tool_name(call) or str(call.get("title") or ""),
            tool_input=call.get("rawInput") or {},
            options=list(params.get("options") or []),
            parked_at=datetime.now().isoformat())
        with self._lock:
            if self._closed:
                return CANCELLED
            park_id = next(self._ids)
            self._parks[park_id] = park
        try:
            self._announce(park)
            decision = park.future.result()
        finally:
            with self._lock:
                self._parks.pop(park_id, None)
                empty = not self._parks
            if empty:
                self._dismiss_notice()
        return self._answer(park, decision)

    def pending(self) -> list[AcpPark]:
        with self._lock:
            return list(self._parks.values())

    def resolve(self, decision: dict, tool_use_id: str = "") -> tuple[bool, str]:
        """Hand an operator's `{behavior, reason}` to the named park, or the
        oldest when none is named. Any thread."""
        with self._lock:
            park = next((p for p in self._parks.values()
                         if not p.future.done()
                         and (not tool_use_id or p.tool_use_id == tool_use_id)),
                        None)
            if park is None:
                return False, "no pending permission request"
            park.future.set_result(decision)
        log.write("acp_permission_resolved", trace_id=self._trace_id,
                  tool_use_id=park.tool_use_id)
        return True, "decision delivered"

    def cancel_all(self, *, close: bool = False) -> None:
        """Answer every park `cancelled` — after `session/cancel`, and for
        good on close, when a request arriving later is cancelled at once."""
        with self._lock:
            self._closed = self._closed or close
            parks = list(self._parks.values())
        for park in parks:
            if not park.future.done():
                park.future.set_result(None)

    def _announce(self, park: AcpPark) -> None:
        from lib.agent_events import PermissionRequested
        _post(PermissionRequested(
            trace_id=park.trace_id, tool_name=park.tool_name,
            tool_use_id=park.tool_use_id, tool_input=park.tool_input,
            kind=park.kind))
        try:
            from lib.agent_messages import event_notify
            from lib.agent_sdk import policy
            event_notify.notify_permission_request(
                trace_id=park.trace_id,
                attrs=policy.notify_attrs(park.kind, park.tool_name,
                                          park.tool_input, park.tool_use_id))
        except Exception:  # noqa: BLE001 — a push must never break the park
            log.error("acp_park_notify_failed", trace_id=park.trace_id,
                      exc_info=True)

    def _dismiss_notice(self) -> None:
        try:
            from lib.agent_messages import event_notify
            event_notify.resolve_permission(self._trace_id)
        except Exception:  # noqa: BLE001 — a stale card beats a crash
            log.error("acp_park_dismiss_failed", trace_id=self._trace_id,
                      exc_info=True)

    @staticmethod
    def _answer(park: AcpPark, decision) -> dict:
        if decision is None:
            post_resolution(park.trace_id, park.tool_use_id, park.tool_name,
                            "cancelled", "regin ACP channel: cancelled")
            return CANCELLED
        from lib.agent_sdk import policy
        behavior, detail = policy.decision_outcome(decision)
        post_resolution(park.trace_id, park.tool_use_id, park.tool_name,
                        behavior, detail)
        return selected_outcome(park.options, behavior)
