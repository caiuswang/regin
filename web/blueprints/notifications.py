"""Realtime notification push: one Server-Sent Events stream + a loopback trigger.

`/api/notifications/stream` carries the nav badge counters (pending schema
drift, unread inbox) as unnamed frames, plus named `notification` /
`resolved` frames for the notification surfaces (toasts, the blocker banner).
A client gets one counts frame on connect and another whenever a number moves
— there is no server-side tick and no client poll.

Counts stay the *unnamed* event so `EventSource.onmessage` keeps its original
meaning; anything richer arrives under a name and is opt-in per listener. The
inbox-row event is named `notification`, not `message`: SSE's default event
name *is* `message`, so that name would also fire every counts listener.

SSE rather than a WebSocket because the traffic is one-way and rare: an
ordinary streaming response needs no dependency, no protocol upgrade, and no
shared connection state, so each stream is written only by the request thread
that owns it.

The stream is authenticated with a single-use ticket from
`POST /api/auth/stream-ticket` (an ordinary bearer-authenticated call), not
with the JWT itself — `EventSource` cannot set an Authorization header, and
`lib.notifications.tickets` explains why the query string is no place for a
week-long credential.

Addressed frames need to know who is listening and to what. The role is
resolved from the redeemed id here rather than read off the JWT, so a demotion
takes effect on the next connect instead of when a week-long token expires. The
*topics* cannot ride the opening GET, because the set changes while the stream
is held open — the tab names itself with a `stream` id on connect and then
declares its interests through `POST /api/notifications/stream/topics`, an
ordinary bearer call. That id is the client's to choose; the ownership check in
`hub.set_topics` is what stops one account redirecting another's stream.
"""

from __future__ import annotations

import json
import queue

from flask import Blueprint, Response, jsonify, request

from lib.activity_log import get_activity_logger
from lib.auth import get_current_user, get_user
from lib.notifications import hub, tickets

notifications_bp = Blueprint('notifications', __name__)

log = get_activity_logger("notifications")

STREAM_PATH = '/api/notifications/stream'
TOPICS_PATH = '/api/notifications/stream/topics'
KEEPALIVE_SECONDS = 25
_LOOPBACK_ADDRS = frozenset({'127.0.0.1', '::1'})

# A stream id is an opaque client-chosen label, and topics are client-chosen
# strings. Neither is trusted, so both are bounded before they become keys a
# subscription holds for the life of a connection.
_MAX_STREAM_ID = 128
_MAX_TOPICS = 64
_MAX_TOPIC_LEN = 200


@notifications_bp.route('/api/auth/stream-ticket', methods=['POST'])
def api_auth_stream_ticket():
    """Exchange the caller's JWT for a ticket the stream URL can carry."""
    user = get_current_user()
    if user is None:
        return jsonify({'error': 'Authentication required'}), 401
    return jsonify({'ticket': tickets.issue(user['id']),
                    'expires_in': tickets.TTL_SECONDS})


@notifications_bp.route(STREAM_PATH)
def api_notifications_stream():
    user_id = tickets.redeem(request.args.get('ticket') or '')
    if user_id is None:
        return jsonify({'error': 'Authentication required'}), 401
    stream_id = (request.args.get('stream') or '')[:_MAX_STREAM_ID] or None
    return Response(
        _frames(user_id, _role_of(user_id), stream_id),
        mimetype='text/event-stream',
        # Any buffering layer holds frames back until the response ends, which
        # for a response that never ends means forever.
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


def _role_of(user_id: int) -> str | None:
    """The role to gate addressed frames with, or None when the id no longer
    names a user.

    None is a closed gate, not an open one (`hub._ROLE_RANK`), and it does not
    close the stream: the ticket already proved authentication, and the badge
    counters it carries are the same for every account. Only the frames that
    ask for a privilege floor are withheld.
    """
    user = get_user(user_id)
    return user['role'] if user else None


@notifications_bp.route(TOPICS_PATH, methods=['POST'])
def api_notifications_stream_topics():
    """Declare which topics an already-open stream wants.

    `{"stream": "<id the stream was opened with>", "topics": [...]}`. Replaces
    the set outright — a client sends what it wants now, not a delta, so a tab
    that navigates away from a trace stops receiving it without a retraction
    call it might never make.
    """
    user = get_current_user()
    if user is None:
        return jsonify({'error': 'Authentication required'}), 401
    body = request.get_json(silent=True) or {}
    topics = body.get('topics')
    if not isinstance(topics, list):
        return jsonify({'error': 'topics must be a list'}), 400
    stream_id = str(body.get('stream') or '')[:_MAX_STREAM_ID]
    updated = hub.set_topics(
        stream_id, user['id'],
        [str(t)[:_MAX_TOPIC_LEN] for t in topics[:_MAX_TOPICS]])
    if not updated:
        # Also the answer for a live stream belonging to someone else: saying
        # which of the two it is would confirm the id exists.
        return jsonify({'error': 'no such stream'}), 404
    return jsonify({'ok': True, 'streams': updated})


def _frames(user_id: int, role: str | None = None,
            stream_id: str | None = None):
    q, first = hub.subscribe(user_id=user_id, role=role, stream_id=stream_id)
    log.read("stream_opened", user_id=user_id,
             subscribers=hub.subscriber_count())
    try:
        yield _encode(hub.COUNTS_EVENT, first)
        while True:
            try:
                yield _encode(*q.get(timeout=KEEPALIVE_SECONDS))
            except queue.Empty:
                # A dead peer is only discovered by writing to it, so an idle
                # stream still has to emit for either side to notice. A named
                # event rather than an SSE comment: EventSource never surfaces
                # comments, so a client staleness check could not see one.
                yield 'event: ping\ndata: {}\n\n'
    finally:
        hub.unsubscribe(q)
        log.read("stream_closed", user_id=user_id,
                 subscribers=hub.subscriber_count())


def _encode(event: str, payload: dict) -> str:
    data = f"data: {json.dumps(payload)}\n\n"
    return data if event == hub.COUNTS_EVENT else f"event: {event}\n{data}"


@notifications_bp.route('/api/internal/notify', methods=['POST'])
def api_internal_notify():
    """Producer-side trigger: fan out an event, then recompute the counters.

    The body is optional. `{"message_id": N}` pushes that inbox row to the
    notification surfaces; `{"resolved": {...}}` retires a blocker whose
    prompt was answered; `{"successor": {"trace_id": ...}}` moves a page off a
    `/clear`ed session onto the one that replaced it. Either way the counters
    follow, so a client that ignores the named frame still sees the badge
    move.

    The row is re-read here rather than carried in the body: the producer is
    a separate process and the store stays the single source of truth, so a
    stale or hand-crafted payload cannot reach a stream.

    Loopback-only — the endpoint is unauthenticated (hooks have no JWT), so
    it must not be reachable off-host.
    """
    if (request.remote_addr or '') not in _LOOPBACK_ADDRS:
        return jsonify({'error': 'not found'}), 404
    body = request.get_json(silent=True) or {}
    _fan_out(body)
    hub.broadcast_counts()
    return jsonify({'ok': True})


def _fan_out(body: dict) -> None:
    """Raise the named event described by a loopback trigger's body, if any."""
    message_id = body.get('message_id')
    if message_id is not None:
        from lib.agent_messages import store
        row = store.get_message(int(message_id))
        # A row pruned by retention between the write and this trigger has
        # nothing left to show; the counts broadcast still corrects the badge.
        if row is not None and not row.get('is_test'):
            hub.broadcast_event('notification', row)
        return
    resolved = body.get('resolved')
    if resolved:
        hub.broadcast_event('resolved', resolved)
        return
    successor = body.get('successor')
    if successor:
        # Only the predecessor's id is trusted from the body; who inherited it
        # is resolved here through the guarded walk, so a hand-crafted trigger
        # cannot name an arbitrary session for a page to jump onto.
        from lib.agent_bridge import succession
        succession.announce(successor.get('trace_id'))


__all__ = ['notifications_bp', 'STREAM_PATH', 'TOPICS_PATH']
