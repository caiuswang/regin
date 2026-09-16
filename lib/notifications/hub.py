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

Named events are *addressed*, not broadcast: `broadcast_event` takes a
`topic` and a `min_role`, and a subscriber receives the frame only if it
declared that topic and clears that role floor. Addressing is by topic and
role, never by identity — the queue records a user id solely so a later
`set_topics` call can prove it owns the subscription it is redirecting.
Unaddressed frames (`topic=None`, `min_role=None`) still reach everyone, which
is what `counts`, `notification` and `resolved` rely on.

Scope: this is in-process fan-out. Under a multi-worker deployment each worker
would reach only its own subscribers, and the push would need a shared bus
(Redis pub/sub or similar) to cross that boundary.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from lib.activity_log import get_activity_logger

log = get_activity_logger("notifications")

_QUEUE_DEPTH = 16

COUNTS_EVENT = "counts"

# Raised by the span ingest, once per accepted batch, naming the trace that
# grew. The trace view suspends its live poll once a session stops changing, so
# this is what tells it the session moved again — without it a session that
# goes quiet and then resumes would wait for a human to scroll.
TRACE_SPANS_EVENT = "trace_spans"

# Raised when a `/clear`ed session's replacement becomes resolvable, naming the
# PREDECESSOR as `trace_id` — the id the stranded page is mounted on.
#
# One frame, two publishers, because the two tiers learn the succession in
# different places. For a terminal session both halves of the fact are written
# out-of-process and in either order, so `agent_bridge.succession` raises it
# from whichever site observes the second one. A run regin launched has no pane
# for that walk to follow, and its own reader is what hears the CLI name the
# new child, so `agent_sdk.registry` raises it there instead.
SUCCESSOR_EVENT = "session_successor"

# Ordered least- to most-privileged. A role outside this table — including
# the `None` of a subscription whose identity could not be resolved — clears
# no floor at all: an unknown privilege is not a proven one.
_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}


@dataclass(frozen=True)
class _Sub:
    """What a queue is entitled to receive."""

    user_id: int | None = None
    role: str | None = None
    stream_id: str | None = None
    topics: frozenset[str] = field(default_factory=frozenset)


_lock = threading.Lock()
_subscribers: dict[queue.Queue, _Sub] = {}

# Ordering guard for read-then-enqueue. Always taken before `_lock`, never
# the other way round.
_read_lock = threading.Lock()


def subscribe(user_id: int | None = None, role: str | None = None,
              stream_id: str | None = None) -> tuple[queue.Queue, dict]:
    """Join the fan-out and take the first frame in one atomic step.

    The caller passes what the route already knows. `role` gates addressed
    frames; `user_id` and `stream_id` exist only so `set_topics` can find this
    subscription again and prove the caller owns it. A subscription starts
    interested in nothing, so an addressed frame reaches it only after it says
    what it wants.
    """
    q: queue.Queue = queue.Queue(maxsize=_QUEUE_DEPTH)
    with _read_lock:
        counts = current_counts()
        with _lock:
            _subscribers[q] = _Sub(user_id=user_id, role=role,
                                   stream_id=stream_id)
    return q, counts


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        _subscribers.pop(q, None)


def set_topics(stream_id: str, user_id: int, topics: Iterable[str]) -> int:
    """Replace the topic interests of the streams `user_id` opened under
    `stream_id`, returning how many were updated.

    Zero means no live stream matched — which is also the answer when the
    stream belongs to somebody else, because the owner check is part of the
    match. A reconnecting tab reuses its stream id, so briefly two
    subscriptions can carry it; both are updated rather than picking one.
    """
    if not stream_id:
        return 0
    wanted = frozenset(topics)
    updated = 0
    with _lock:
        for q, sub in list(_subscribers.items()):
            if sub.stream_id == stream_id and sub.user_id == user_id:
                _subscribers[q] = replace(sub, topics=wanted)
                updated += 1
    return updated


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


def broadcast_event(name: str, payload: dict, *, topic: str | None = None,
                    min_role: str | None = None) -> None:
    """Hand one named notification event to the streams entitled to it.

    `topic` addresses the frame: only subscribers that declared that exact
    topic receive it. `min_role` sets a privilege floor. Leaving both unset
    reaches every open stream, which is what the unaddressed frames rely on.

    Takes `_read_lock` so an event and the counts frame raised by the same
    write reach each queue in the order they were raised — a toast that
    arrived before its badge moved would render the old number.
    """
    if name == COUNTS_EVENT:
        raise ValueError("use broadcast_counts() for counts frames")
    if min_role is not None and min_role not in _ROLE_RANK:
        # Silently treating a typo as "no floor" would ship an open gate that
        # reads as a closed one.
        raise ValueError(f"unknown role: {min_role!r}")
    with _read_lock:
        targets = _targets(topic=topic, min_role=min_role)
        if not targets:
            return
        for q in targets:
            _offer(q, (name, payload))


def _targets(*, topic: str | None = None,
             min_role: str | None = None) -> list[queue.Queue]:
    with _lock:
        if topic is None and min_role is None:
            return list(_subscribers)
        return [q for q, sub in _subscribers.items()
                if _entitled(sub, topic, min_role)]


def _entitled(sub: _Sub, topic: str | None, min_role: str | None) -> bool:
    if topic is not None and topic not in sub.topics:
        return False
    if min_role is None:
        return True
    return _ROLE_RANK.get(sub.role or "", -1) >= _ROLE_RANK[min_role]


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


__all__ = ["subscribe", "unsubscribe", "set_topics", "subscriber_count",
           "current_counts", "broadcast_counts", "broadcast_event",
           "COUNTS_EVENT"]
