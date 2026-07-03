"""Notification event-bus catalog API (`lib.agent_messages.events`).

`GET /api/events/kinds` enumerates every declared notifiable event kind
(severity + current enablement) — the same registry `regin events list`
reads and the inbox UI can render as a legend of "what can notify you".
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from lib.agent_messages import events

events_bp = Blueprint("events", __name__)


@events_bp.route("/api/events/kinds")
def api_events_kinds():
    return jsonify({"kinds": events.catalog()})
