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
"""

from __future__ import annotations

import json
import queue

from flask import Blueprint, Response, jsonify, request

from lib.activity_log import get_activity_logger
from lib.auth import get_current_user
from lib.notifications import hub, tickets

notifications_bp = Blueprint('notifications', __name__)

log = get_activity_logger("notifications")

STREAM_PATH = '/api/notifications/stream'
KEEPALIVE_SECONDS = 25
_LOOPBACK_ADDRS = frozenset({'127.0.0.1', '::1'})


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
    return Response(
        _frames(user_id),
        mimetype='text/event-stream',
        # Any buffering layer holds frames back until the response ends, which
        # for a response that never ends means forever.
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


def _frames(user_id: int):
    q, first = hub.subscribe()
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
    prompt was answered. Either way the counters follow, so a client that
    ignores the named frame still sees the badge move.

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


__all__ = ['notifications_bp', 'STREAM_PATH']
