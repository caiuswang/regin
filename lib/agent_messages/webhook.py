"""Optional outbound webhook for agent → human messages.

Fires a single JSON POST when a `send_to_user` message clears the
configured severity threshold, so a long-running / background agent can
reach the user out-of-band (ntfy, Slack incoming webhook, a phone push
bridge, …) instead of only landing in the in-app inbox.

Opt-in: with no `settings.agent_messages.webhook_url`, dispatch is a
no-op. Never raises — delivery is best-effort and its outcome is recorded
on the message row (`webhook_status`) for triage, not retried.
"""

from __future__ import annotations

import json
import urllib.request

from lib.activity_log import get_activity_logger
from lib.orm.models.agent_messages import severity_rank
from lib.settings import settings

log = get_activity_logger("agent_messages")


def _payload(msg: dict) -> dict:
    """The JSON body POSTed to the webhook for one message."""
    trace_id = msg.get("trace_id") or ""
    base = settings.agent_messages.base_url.rstrip("/")
    return {
        "event": "agent_message",
        "type": msg.get("msg_type"),
        "title": msg.get("title"),
        "body": msg.get("body"),
        "links": msg.get("links"),
        "session_id": trace_id,
        "session_url": f"{base}/trace/sessions/{trace_id}" if trace_id else None,
        "timestamp": msg.get("created_at"),
    }


def should_dispatch(msg_type: str | None) -> bool:
    """True when a webhook is configured AND the type clears the threshold."""
    cfg = settings.agent_messages
    if not cfg.webhook_url:
        return False
    return severity_rank(msg_type) >= severity_rank(cfg.webhook_min_severity)


def maybe_dispatch(msg: dict) -> str | None:
    """POST `msg` to the webhook if its severity clears the gate.

    Returns 'sent' | 'failed' | 'skipped', or None when no webhook is
    configured at all (so the caller can leave `webhook_status` NULL).
    """
    cfg = settings.agent_messages
    if not cfg.webhook_url:
        return None
    if not should_dispatch(msg.get("msg_type")):
        return "skipped"
    body = json.dumps(_payload(msg)).encode("utf-8")
    req = urllib.request.Request(
        cfg.webhook_url, data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.webhook_timeout_seconds):
            log.write("webhook_sent", msg_type=msg.get("msg_type"),
                      trace_id=msg.get("trace_id"))
            return "sent"
    except Exception:
        log.error("webhook_failed", exc_info=True)
        return "failed"


__all__ = ["maybe_dispatch", "should_dispatch"]
