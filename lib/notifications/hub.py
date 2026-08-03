"""Fan-out of nav badge counters and notification events to every open stream.

Nothing here polls. `broadcast_counts()` / `broadcast_event()` are *signals*,
raised by whoever just mutated the source of truth — the Flask routes that mark
inbox messages read or resolve drift findings, and (over loopback, via
`lib.notifications.notify`) the out-of-process producers that write the rows.

Each subscriber owns a queue that only its own request thread drains, writing
to its own response, so there is no shared connection state and no dispatcher:
a slow client backs up its own queue and nobody else's.

Two frame kinds share the queue:

  * ``counts`` — absolute badge numbers. Idempotent, so overflowing a queue can
    drop an older one and lose nothing. That only holds while frames reach a
    queue in the order their counts were read, which is why `_read_lock` spans
    the read *and* the enqueue: two threads reading in one order and enqueueing
    in the other would leave the badge on the older number, with nothing to
    correct it. Handing a new subscriber its first frame takes the same lock,
    so a broadcast cannot slip between subscribing and reading.
  * ``notification`` / ``resolved`` — one-shot events driving the toast and
    blocker surfaces. (Named `notification`, not `message`: SSE's *default*
    event name is `message`, so that name would also fire every counts
    listener.) These are not idempotent, but overflow still drops the queue's
    head rather than picking a victim — `_offer` explains why reordering to
    save one is worse than losing it. A stalled client loses whatever the
    dropped frame carried: a missed ``notification`` costs a toast (the row is
    still in the inbox), while a missed ``resolved`` strands a blocker banner
    until that tab reloads, since nothing re-sends it. Only the badge is
    self-healing, from the next counts frame.

Scope: this is in-process fan-out. Under a multi-worker deployment each worker
would reach only its own subscribers, and the push would need a shared bus
(Redis pub/sub or similar) to cross that boundary.
"""

from __future__ import annotations

import queue
import threading

from lib.activity_log import get_activity_logger

log = get_activity_logger("notifications")

_QUEUE_DEPTH = 16

COUNTS_EVENT = "counts"

_lock = threading.Lock()
_subscribers: set[queue.Queue] = set()

# Ordering guard for read-then-enqueue. Always taken before `_lock`, never
# the other way round.
_read_lock = threading.Lock()


def subscribe() -> tuple[queue.Queue, dict]:
    """Join the fan-out and take the first frame in one atomic step."""
    q: queue.Queue = queue.Queue(maxsize=_QUEUE_DEPTH)
    with _read_lock:
        counts = current_counts()
        with _lock:
            _subscribers.add(q)
    return q, counts


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        _subscribers.discard(q)


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)


def current_counts() -> dict:
    """The badge numbers (and the inbox's urgency), read fresh from source."""
    from lib.agent_messages import store
    from lib.trace.payload_drift_store import pending_drift_count

    return {
        "drift_pending": pending_drift_count(),
        "inbox_unread": store.unread_count(),
        # The badge's colour, not just its number: a parked agent and a stack
        # of progress lines are both "3 unread" and want different urgency.
        "inbox_severity": store.unread_top_severity(),
    }


def broadcast_counts() -> None:
    """Recompute both counters and hand them to every open stream."""
    with _read_lock:
        targets = _targets()
        if not targets:
            return
        counts = current_counts()
        for q in targets:
            _offer(q, (COUNTS_EVENT, counts))


def broadcast_event(name: str, payload: dict) -> None:
    """Hand one named notification event to every open stream.

    Takes `_read_lock` so an event and the counts frame raised by the same
    write reach each queue in the order they were raised — a toast that
    arrived before its badge moved would render the old number.
    """
    if name == COUNTS_EVENT:
        raise ValueError("use broadcast_counts() for counts frames")
    with _read_lock:
        targets = _targets()
        if not targets:
            return
        for q in targets:
            _offer(q, (name, payload))


def _targets() -> list[queue.Queue]:
    with _lock:
        return list(_subscribers)


def _offer(q: queue.Queue, frame: tuple[str, dict]) -> None:
    try:
        q.put_nowait(frame)
    except queue.Full:
        # Drop the head, never reorder. An earlier version drained the queue to
        # shed a stale `counts` frame in preference to a one-shot event, but
        # `_read_lock` serializes only the *producers* — the stream's own
        # request thread drains the same queue with no lock, so a frame it took
        # mid-drain would be written to the socket ahead of frames the producer
        # was still holding. That reorders `notification` after the `resolved`
        # that answers it, stranding a banner for an already-answered prompt.
        # Dropping the head costs whatever that frame carried (see the module
        # docstring) — a smaller, and above all predictable, loss.
        log.write("stream_frame_shed", frame_event=frame[0],
                  reason="subscriber_queue_full")
        _drop_oldest(q)
        try:
            q.put_nowait(frame)
        except queue.Full:
            # Not merely shed but lost outright — a dropped `resolved` strands
            # a banner until that tab reloads, so it is worth more than INFO.
            log.warn("stream_frame_dropped", frame_event=frame[0])


def _drop_oldest(q: queue.Queue) -> None:
    try:
        q.get_nowait()
    except queue.Empty:
        return


__all__ = ["subscribe", "unsubscribe", "subscriber_count", "current_counts",
           "broadcast_counts", "broadcast_event", "COUNTS_EVENT"]
