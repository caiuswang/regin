"""Realtime badge push: one Server-Sent Events stream + a loopback trigger.

`/api/notifications/stream` carries both nav badge counters (pending schema
drift, unread inbox). A client gets one frame on connect and another whenever
a number moves — there is no server-side tick and no client poll.

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
        yield _encode(first)
        while True:
            try:
                yield _encode(q.get(timeout=KEEPALIVE_SECONDS))
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


def _encode(counts: dict) -> str:
    return f"data: {json.dumps(counts)}\n\n"


@notifications_bp.route('/api/internal/notify', methods=['POST'])
def api_internal_notify():
    """Producer-side trigger: recompute the counters and fan them out.

    Loopback-only — the payload carries no data, but the endpoint is
    unauthenticated (hooks have no JWT), so it must not be reachable off-host.
    """
    if (request.remote_addr or '') not in _LOOPBACK_ADDRS:
        return jsonify({'error': 'not found'}), 404
    hub.broadcast_counts()
    return jsonify({'ok': True})


__all__ = ['notifications_bp', 'STREAM_PATH']
