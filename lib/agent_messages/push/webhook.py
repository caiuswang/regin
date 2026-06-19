"""Generic webhook push channel.

POSTs a single JSON body to `settings.agent_messages.webhook_url` — the
provider-neutral channel for ntfy, a Slack incoming webhook, a phone
push bridge, or any HTTP endpoint that accepts the payload below.
"""

from __future__ import annotations

from lib.agent_messages.push import base
from lib.agent_messages.push.base import PushChannel, PushMessage


class WebhookChannel(PushChannel):
    channel_id = "webhook"
    display_name = "Webhook"

    def is_configured(self) -> bool:
        return bool(self.cfg.webhook_url)

    def min_severity(self) -> str:
        return self.cfg.webhook_min_severity

    def _payload(self, msg: PushMessage) -> dict:
        return {
            "event": "agent_message",
            "type": msg.msg_type,
            "title": msg.title,
            "body": msg.body,
            "links": msg.links,
            "session_id": msg.session_id,
            "session_url": msg.session_url,
            "timestamp": msg.timestamp,
        }

    def deliver(self, msg: PushMessage) -> None:
        base.http_post_json(self.cfg.webhook_url, self._payload(msg),
                            timeout=self.cfg.webhook_timeout_seconds)


__all__ = ["WebhookChannel"]
