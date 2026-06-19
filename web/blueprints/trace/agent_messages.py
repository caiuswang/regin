"""Agent → human message endpoints (the `send_to_user` inbox).

Reads/writes go through `lib.agent_messages.store`, the canonical
`agent_messages` table. Two surfaces:

  * per-session feed  — `/api/sessions/<id>/agent-messages` (Messages tab)
  * cross-session inbox — `/api/agent-messages/*` (Inbox view + nav badge)

The per-session feed falls back to the legacy span-derived query for
sessions captured before `agent_messages` existed, so historical traces
still render their progress feed.
"""

from __future__ import annotations

from flask import request, jsonify

from lib.agent_messages import store
from web.blueprints.trace import trace_bp


def _bool_arg(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return default
    return raw.lower() in ('1', 'true', 'yes')


def _session_goal(session_id: str):
    """First user prompt — frames the per-session feed."""
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT json_extract(attributes, '$.text') as text
            FROM session_spans
            WHERE trace_id = ? AND name = 'prompt'
              AND json_extract(attributes, '$.text') IS NOT NULL
            ORDER BY start_time ASC LIMIT 1
        """, (session_id,)).fetchone()
        return row['text'] if row else None
    finally:
        conn.close()


def _legacy_span_messages(session_id: str) -> list[dict]:
    """Span-derived feed for pre-`agent_messages` sessions, mapped into the
    same shape the store emits so the UI renders one way."""
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT span_id,
                   MAX(json_extract(attributes, '$.user_message')) as message,
                   MIN(start_time) as ts
            FROM session_spans
            WHERE trace_id = ?
              AND status_code != 'PENDING'
              AND json_extract(attributes, '$.tool_name') LIKE 'mcp__%send_to_user%'
              AND json_extract(attributes, '$.user_message') IS NOT NULL
            GROUP BY span_id
            ORDER BY ts ASC
        """, (session_id,)).fetchall()
        return [{
            'id': None, 'span_id': r['span_id'], 'msg_type': 'progress',
            'title': None, 'body': r['message'], 'links': None,
            'created_at': r['ts'], 'pinned': False, 'version': 1,
            'read_at': None, 'webhook_status': None,
        } for r in rows]
    finally:
        conn.close()


@trace_bp.route('/api/sessions/<session_id>/agent-messages')
def api_session_agent_messages(session_id):
    """send_to_user messages for one session, oldest first."""
    messages = store.list_session_messages(session_id)
    if not messages:
        messages = _legacy_span_messages(session_id)
    return jsonify({
        'messages': messages,
        'session_goal': _session_goal(session_id),
    })


@trace_bp.route('/api/agent-messages/inbox')
def api_agent_messages_inbox():
    """Cross-session inbox feed (newest first) + the unread count."""
    include_tests = _bool_arg('include_tests')
    unread_only = _bool_arg('unread')
    types_arg = request.args.get('types')
    types = [t for t in types_arg.split(',') if t] if types_arg else None
    try:
        limit = min(int(request.args.get('limit', 200)), 500)
    except (TypeError, ValueError):
        limit = 200
    messages = store.list_inbox(
        unread_only=unread_only, include_tests=include_tests,
        types=types, limit=limit)
    return jsonify({
        'messages': messages,
        'unread_count': store.unread_count(include_tests=include_tests),
    })


@trace_bp.route('/api/agent-messages/unread-count')
def api_agent_messages_unread_count():
    """Just the badge number — cheap enough to poll."""
    return jsonify({'count': store.unread_count(
        include_tests=_bool_arg('include_tests'))})


@trace_bp.route('/api/agent-messages/read', methods=['POST'])
def api_agent_messages_read():
    """Mark a batch of messages read. Body: {ids: [int, …]}."""
    payload = request.get_json(silent=True) or {}
    ids = [i for i in (payload.get('ids') or []) if isinstance(i, int)]
    return jsonify({'marked': store.mark_read(ids)})


@trace_bp.route('/api/agent-messages/<int:message_id>/ack', methods=['POST'])
def api_agent_messages_ack(message_id):
    return jsonify({'acked': store.ack(message_id)})


@trace_bp.route('/api/agent-messages/<int:message_id>/dismiss', methods=['POST'])
def api_agent_messages_dismiss(message_id):
    return jsonify({'dismissed': store.dismiss(message_id)})


@trace_bp.route('/api/agent-messages/<int:message_id>/pin', methods=['POST'])
def api_agent_messages_pin(message_id):
    payload = request.get_json(silent=True) or {}
    pinned = bool(payload.get('pinned', True))
    return jsonify({'ok': store.set_pinned(message_id, pinned)})
