"""Parking interactive tool calls until the operator answers from `/live`."""

from __future__ import annotations

import asyncio
from datetime import datetime

from lib.agent_events import PermissionRequested, PermissionResolved
from . import client, policy, registry
from .answers import build_updated_input
from .runner_support import log


class _ParkMixin:
    """Permission and ask handling for `AgentRunner`."""

    async def _can_use_tool(self, tool_name: str, tool_input: dict, context):
        kind = policy.permission_kind(tool_name)
        if kind is None:
            return client.allow(tool_input)
        return await self._park(kind, tool_name, tool_input, context)

    async def _park(self, kind: str, tool_name: str, tool_input: dict, context):
        """Hold the call open until the operator resolves it from `/live`.

        The ask is registered BEFORE anything announces it. The notification
        frame triggers every open tab to re-read the blocker feed, and the
        feed derives an owned session's parked state from the registry — a
        frame that races ahead of its own registration makes that read come
        back empty and take down the very banner the frame just raised.
        """
        tool_use_id = getattr(context, "tool_use_id", "") or ""
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        ask_id = registry.register_ask(registry.PendingAsk(
            trace_id=self.trace_id,
            tool_use_id=tool_use_id,
            tool_input=tool_input,
            future=future,
            loop=asyncio.get_running_loop(),
            kind=kind,
            tool_name=tool_name,
            parked_at=datetime.now().isoformat(),
        ))
        try:
            await self._emit(PermissionRequested(
                trace_id=self.trace_id,
                tool_name=tool_name,
                tool_use_id=tool_use_id,
                tool_input=tool_input,
                kind=kind,
            ))
            await self._notify_park(kind, tool_name, tool_input, tool_use_id)
            resolution = await self._await_operator(future)
        finally:
            # Every exit drops this park and only this one: resolved (already
            # popped, so a no-op) or cancelled by teardown. A park left behind
            # is a call the operator can still be offered and nothing can
            # deliver an answer to.
            registry.discard_ask(ask_id)
            await self._dismiss_park_notice()
        if kind == "question":
            return self._answered(tool_use_id, tool_input, resolution)
        return await self._decided(tool_name, tool_use_id, tool_input,
                                   resolution)

    @property
    def _permission_mode(self) -> str:
        """The mode this run actually launches under, per-run override first."""
        return (self.options.permission_mode if self.options else "") or ""

    async def _notify_park(self, kind: str, tool_name: str, tool_input: dict,
                           tool_use_id: str) -> None:
        """Tell the operator out-of-band that a call is waiting on them.

        A park waits indefinitely, so this push is what keeps that from
        meaning "hangs": without it the only signal a human ever got was a row
        in a trace nobody had open — the hook tier pushes this event, so a
        session regin owns would be the *quiet* one.

        Off the loop thread: the push channels do network I/O and every run on
        this process shares one loop.
        """
        try:
            from lib.agent_messages import event_notify

            attrs = policy.notify_attrs(kind, tool_name, tool_input,
                                        tool_use_id)
            await asyncio.to_thread(
                lambda: event_notify.notify_permission_request(
                    trace_id=self.trace_id, attrs=attrs))
        except Exception:  # noqa: BLE001 — a push must never break the park
            log.error("sdk_park_notify_failed", trace_id=self.trace_id,
                      exc_info=True)

    async def _dismiss_park_notice(self) -> None:
        """Retire the pending card once this session has nothing parked.

        The card is keyed per *session* while parks are keyed per call, so
        dismissing on the first resolution would clear a notice that is still
        true for the calls still waiting.
        """
        try:
            if registry.pending_asks(self.trace_id):
                return
            from lib.agent_messages import event_notify

            await asyncio.to_thread(
                lambda: event_notify.resolve_permission(self.trace_id))
        except Exception:  # noqa: BLE001 — as above; a stale card beats a crash
            log.error("sdk_park_dismiss_failed", trace_id=self.trace_id,
                      exc_info=True)

    async def _await_operator(self, future: "asyncio.Future"):
        """Wait for the human — with no clock on it.

        One assistant message can carry several gated calls, so each waits on
        its own future and drops only its own park; the session's other parked
        calls are still someone's to answer.

        A park deliberately never expires. Declining one on a timer refuses a
        call the operator may never have been shown, and the model is told it
        was refused — so the agent goes on to narrate a decision the human did
        not make. An unattended park therefore holds its worker and a
        `max_concurrent_runs` slot until it is answered or the run is stopped.
        """
        return await future

    def _answered(self, tool_use_id: str, tool_input: dict, answers):
        # Reachable only on teardown: nothing else ends a park unresolved.
        if answers is None:
            return client.deny(policy.DISMISSED)
        updated = build_updated_input(tool_input, answers)
        self._answered_asks[tool_use_id] = {
            'questions': (tool_input or {}).get('questions') or [],
            'answers': updated.get('answers') or {},
        }
        return client.allow(updated)

    async def _decided(self, tool_name: str, tool_use_id: str,
                       tool_input: dict, decision):
        """Run or refuse the gated call, and record which of the two happened.

        The resolution span is what a later reader sees: the parked
        `permission.request` placeholder is retired by `tool_use_id`, so
        without this the trace of a denial is a request that simply stops.
        """
        behavior, detail = policy.decision_outcome(decision)
        await self._emit(PermissionResolved(
            trace_id=self.trace_id,
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            behavior=behavior,
            detail=detail,
        ))
        if behavior == "allow":
            return client.allow(tool_input)
        return client.deny(detail)
