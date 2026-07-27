"""Own one regin-launched Claude session end to end.

The runner holds the SDK client, translates every message through the neutral
event union into spans, and parks interactive tool calls until the operator
answers them from `/live`.

Spans are posted over HTTP by `lib.hook_plugin.post_span`, which is blocking, so
every write is pushed to a worker thread — a blocked event loop would stall the
`can_use_tool` callback the answer path depends on.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from lib.activity_log import get_activity_logger
from lib.agent_events import PermissionRequested, ToolCall, ToolResult, to_span
from lib.agent_events.from_sdk import from_sdk_message, prompt_event
from lib.settings import settings
from . import client, registry, store
from .answers import build_updated_input

log = get_activity_logger("agent_sdk")

# Tools that block on a human rather than returning on their own. Only these
# park a future; everything else is auto-allowed, because regin's permission
# story for SDK runs is the operator answering questions, not gating every tool.
_INTERACTIVE_TOOLS = frozenset({"AskUserQuestion"})


class RunnerBusy(RuntimeError):
    """`max_concurrent_runs` reached."""


class AgentRunner:
    """One live session. Not reusable across runs."""

    def __init__(self, trace_id: str, *, cwd: str | None = None):
        self.trace_id = trace_id
        self.cwd = cwd
        self._client = None
        self._tool_names: dict[str, str] = {}

    async def _post(self, span: dict | None) -> None:
        if not span:
            return
        from lib.hook_plugin import post_span

        await asyncio.to_thread(lambda: post_span(**span))

    async def _emit(self, event) -> None:
        await self._post(to_span(self._name_tool(event)))

    def _name_tool(self, event):
        """Tool results carry only a `tool_use_id`, so the call's name has to be
        carried forward from the `ToolCall` to name the resolved span."""
        if isinstance(event, ToolCall) and event.tool_use_id:
            self._tool_names[event.tool_use_id] = event.tool_name
            return event
        if isinstance(event, ToolResult) and not event.tool_name:
            name = self._tool_names.pop(event.tool_use_id, '') or 'unknown'
            return replace(event, tool_name=name)
        return event

    async def _can_use_tool(self, tool_name: str, tool_input: dict, context):
        if tool_name not in _INTERACTIVE_TOOLS:
            return client.allow(tool_input)
        return await self._park_question(tool_name, tool_input, context)

    async def _park_question(self, tool_name: str, tool_input: dict, context):
        tool_use_id = getattr(context, "tool_use_id", "") or ""
        await self._emit(PermissionRequested(
            trace_id=self.trace_id,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            tool_input=tool_input,
            kind="question",
        ))
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        registry.register_ask(registry.PendingAsk(
            trace_id=self.trace_id,
            tool_use_id=tool_use_id,
            tool_input=tool_input,
            future=future,
            loop=asyncio.get_running_loop(),
        ))
        try:
            answers = await future
        except asyncio.CancelledError:
            registry.discard_ask(self.trace_id)
            raise
        if answers is None:
            return client.deny("Dismissed by operator")
        return client.allow(build_updated_input(tool_input, answers))

    async def start(self) -> None:
        if registry.active_run_count() >= settings.agent_sdk.max_concurrent_runs:
            raise RunnerBusy("max_concurrent_runs reached")
        self._client = client.new_client(
            cwd=self.cwd, can_use_tool=self._can_use_tool)
        await self._client.connect()
        registry.register_run(self.trace_id, self)
        store.upsert_run(self.trace_id, status="running", cwd=self.cwd,
                         model=settings.agent_sdk.model or None)

    async def submit(self, text: str) -> None:
        """Send a prompt and drain the turn it produces."""
        await self._emit(prompt_event(self.trace_id, text))
        await self._client.query(text)
        async for message in self._client.receive_response():
            for event in from_sdk_message(self.trace_id, message):
                await self._emit(event)

    async def interrupt(self) -> None:
        await self._client.interrupt()

    async def stop(self, *, status: str = "exited", detail: str = "") -> None:
        registry.unregister_run(self.trace_id)
        if self._client is not None:
            await self._client.disconnect()
        store.upsert_run(self.trace_id, status=status, detail=detail or None)


async def run_once(trace_id: str, prompt: str, *, cwd: str | None = None) -> None:
    """Launch, run one prompt to completion, and tear down."""
    if not settings.agent_sdk.enabled:
        raise RuntimeError("agent_sdk disabled")
    runner = AgentRunner(trace_id, cwd=cwd)
    await runner.start()
    try:
        await runner.submit(prompt)
    except Exception as exc:
        log.error("sdk_run_failed", trace_id=trace_id, detail=str(exc))
        await runner.stop(status="failed", detail=str(exc))
        raise
    await runner.stop()
