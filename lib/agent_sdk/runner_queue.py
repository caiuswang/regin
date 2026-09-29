"""The prompt queue a long-lived session drains one turn at a time."""

from __future__ import annotations

import asyncio

from lib.settings import settings
from .runner_support import _CLOSE, _Queued, log


class _QueueMixin:
    """Queued-prompt handling for `AgentRunner`."""

    def enqueue(self, text: str, *, waiting: bool = True,
                images: list | None = None, row_id: int | None = None) -> None:
        """Queue a prompt. Must run on this runner's loop — see
        `registry.submit_prompt` for the cross-thread entry point.

        `waiting=False` for the prompt a run was launched with: it is the
        run's first turn, not a message queued behind one, and reporting it as
        waiting puts a steering chip on the card for the gap between the run
        becoming reachable and its pump starting — which is exactly when an
        operator opens `/live` on a run they just launched. Keeping it out of
        the mirror also keeps it un-editable, which is right for the same
        reason: it is already on its way to the CLI.
        """
        with self._queue_lock:
            self._queue_seq += 1
            item = _Queued(id=f"q{self._queue_seq}", text=text,
                           images=list(images or []), row_id=row_id)
            if waiting:
                self._pending.append(item)
        self._prompts.put_nowait(item)
        # `submit_prompt` acks the moment `call_soon_threadsafe` ACCEPTS this
        # call, which a stopped loop also does; this line is the first proof
        # the prompt actually reached the queue.
        log.write("sdk_prompt_enqueued", trace_id=self.trace_id,
                  prompt_id=item.id, row_id=row_id, chars=len(text),
                  waiting=waiting)

    def _report_drop(self, item: "_Queued", reason: str) -> None:
        """Record a prompt the session accepted and will never run.

        Every abandonment used to be silent — the queue is in-memory, the
        composer had already cleared the operator's draft on `submit_prompt`'s
        ack, and the `bridge_messages` row still read `delivered / "prompt
        queued"`. So a lost prompt left no artifact anywhere and could only be
        inferred from a missing span. Both surfaces are written here: the log
        for diagnosis, the row for the operator looking at the Messages tab.
        """
        if item.dropped:
            return
        item.dropped = True
        log.error("sdk_prompt_dropped", trace_id=self.trace_id,
                  prompt_id=item.id, row_id=item.row_id, reason=reason,
                  chars=len(item.text), text_head=item.text[:120])
        if item.row_id is None:
            return
        try:
            from lib.agent_bridge import store as bridge_store
            bridge_store.mark_dropped(item.row_id, reason)
        except Exception as exc:
            log.error("sdk_prompt_drop_unrecorded", trace_id=self.trace_id,
                      prompt_id=item.id, row_id=item.row_id, detail=str(exc))

    def _drain_pending(self, reason: str) -> None:
        """Report every prompt still waiting when the session stops running."""
        with self._queue_lock:
            stranded = list(self._pending)
            self._pending.clear()
        for item in stranded:
            self._report_drop(item, reason)

    def pending_prompts(self) -> list[dict]:
        """Prompts queued behind the running turn, oldest first. Safe to call
        from any thread — a snapshot, never the live list."""
        with self._queue_lock:
            return [{'id': q.id, 'text': q.text} for q in self._pending]

    def _find_pending(self, prompt_id: str) -> "_Queued | None":
        """The still-queued record `prompt_id` names. Caller holds the lock."""
        return next((q for q in self._pending if q.id == prompt_id), None)

    def edit_pending(self, prompt_id: str, text: str) -> tuple[bool, str]:
        """Rewrite a queued prompt in place. Safe to call from any thread.

        In place, keeping its slot: an operator fixing a typo in the third
        queued message meant to fix that message, not to move it behind the
        two after it.
        """
        with self._queue_lock:
            item = self._find_pending(prompt_id)
            if item is None:
                return False, "that prompt is no longer queued"
            item.text = text
        return True, "prompt updated"

    def cancel_pending(self, prompt_id: str) -> tuple[bool, str]:
        """Drop a queued prompt before its turn starts. Any thread.

        The record's abandonment is accounted for here, which is what `dropped`
        marks — the pump reaches it only once the current turn ends, and
        reporting a *drop* there resurrected the cancelled text as a chip the
        moment the session went idle.
        """
        with self._queue_lock:
            item = self._find_pending(prompt_id)
            if item is None:
                return False, "that prompt is no longer queued"
            item.cancelled = True
            item.dropped = True
            self._pending.remove(item)
        self._record_cancel(item)
        return True, "prompt removed"

    def _record_cancel(self, item: "_Queued") -> None:
        """Stop a cancelled prompt's row reading `delivered` without earning it
        the chip a drop earns. A failure to record must not fail the cancel —
        the operator's removal already happened."""
        if item.row_id is None:
            return
        try:
            from lib.agent_bridge import store as bridge_store
            bridge_store.mark_cancelled(item.row_id)
        except Exception as exc:
            log.error("sdk_prompt_cancel_unrecorded", trace_id=self.trace_id,
                      prompt_id=item.id, row_id=item.row_id, detail=str(exc))

    async def _await_prompt(self) -> "_Queued | None":
        """Block for the next queued record, raising `TimeoutError` on stream
        silence.

        With `idle_timeout_sec` 0 — the default — this waits indefinitely: an
        interactive session belongs to the operator until they stop it. A
        configured timeout measures *frames*, not the prompt queue, but frames
        are a weak liveness signal — a background subagent or long shell task
        emits none between task notifications — so a timeout can still reap a
        session whose child is mid-work. That trade is acceptable only for
        unattended runs.
        """
        timeout = int(settings.agent_sdk.idle_timeout_sec or 0)
        if timeout <= 0:
            return await self._prompts.get()
        while True:
            seen = self._frames
            try:
                return await asyncio.wait_for(self._prompts.get(), timeout)
            except asyncio.TimeoutError:
                if self._frames == seen:
                    raise

    def _take(self, item: "_Queued") -> bool:
        """Claim `item` for the turn about to run, or report it cancelled.

        The mirror pops with the queue, so a prompt stops reading as "waiting"
        the moment its turn starts — the same poll the real prompt span lands.
        That pop happens under the lock the mutations hold, which is what makes
        the cutoff exact: a rewrite either lands before the CLI is given the
        text, or finds the record gone and refuses.
        """
        with self._queue_lock:
            if item.cancelled:
                return False
            if item in self._pending:
                self._pending.remove(item)
        return True

    async def _next_prompt(self) -> "_Queued | None":
        """The next queued prompt, or None when the session should end.

        A cancelled record is skipped rather than run: it is already inside the
        queue when it is dropped, so this is where a removed prompt actually
        stops existing. Both removers account for its row before it gets here
        (`cancel_pending`, and `request_stop` via `_drain_pending`), so the
        drop below is a backstop for a record neither of them recorded — it is
        a no-op for the ones they did.

        An idle session still holds a `claude` child process, so an abandoned
        one is reclaimed rather than pinned against `max_concurrent_runs`
        forever.
        """
        while True:
            try:
                item = await self._await_prompt()
            except asyncio.TimeoutError:
                self._stop_reason = "idle timeout"
                self._drain_pending("idle_timeout")
                return None
            # `_CLOSE` was never a record, so it falls through untouched.
            if item is _CLOSE:
                self._drain_pending(self._stop_reason or "session_ended")
                return None
            if self._take(item):
                return item
            self._report_drop(item, "cancelled")
