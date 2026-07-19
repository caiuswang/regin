"""Fan-out of the nav badge counters to every open event stream.

Nothing here polls. `broadcast_counts()` is a *signal*, raised by whoever just
mutated a counter's source of truth — the Flask routes that mark inbox
messages read or resolve drift findings, and (over loopback, via
`lib.notifications.notify`) the out-of-process producers that write the rows.

Each subscriber owns a queue that only its own request thread drains, writing
to its own response, so there is no shared connection state and no dispatcher:
a slow client backs up its own queue and nobody else's.

Frames carry absolute counts rather than deltas, so overflowing a queue can
drop the oldest and lose nothing. That only holds while frames reach a queue
in the order their counts were read, which is why `_read_lock` spans the read
*and* the enqueue: two threads reading in one order and enqueueing in the
other would leave the badge on the older number, with nothing to correct it.
Handing a new subscriber its first frame takes the same lock, so a broadcast
cannot slip between subscribing and reading.

Scope: this is in-process fan-out. Under a multi-worker deployment each worker
would reach only its own subscribers, and the push would need a shared bus
(Redis pub/sub or similar) to cross that boundary.
"""

from __future__ import annotations

import queue
import threading

from lib.activity_log import get_activity_logger

log = get_activity_logger("notifications")

_QUEUE_DEPTH = 8

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
    """Both badge numbers, read fresh from their source of truth."""
    from lib.agent_messages import store
    from lib.trace.payload_drift_store import pending_drift_count

    return {
        "drift_pending": pending_drift_count(),
        "inbox_unread": store.unread_count(),
    }


def broadcast_counts() -> None:
    """Recompute both counters and hand them to every open stream."""
    with _read_lock:
        with _lock:
            targets = list(_subscribers)
        if not targets:
            return
        counts = current_counts()
        for q in targets:
            _offer(q, counts)


def _offer(q: queue.Queue, counts: dict) -> None:
    try:
        q.put_nowait(counts)
    except queue.Full:
        _drop_oldest(q)
        q.put_nowait(counts)


def _drop_oldest(q: queue.Queue) -> None:
    try:
        q.get_nowait()
    except queue.Empty:
        return


__all__ = ["subscribe", "unsubscribe", "subscriber_count",
           "current_counts", "broadcast_counts"]
