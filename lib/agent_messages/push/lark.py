"""Lark / Feishu push channel (custom-bot incoming webhook).

Delivers a message to a Lark group via a *custom bot's* webhook URL, so
you can read agent progress from Feishu. Configure `lark_webhook_url`
(the bot's incoming-webhook URL) under `settings.agent_messages`; if the
bot has "signature verification" turned on, also set `lark_secret` and
each request is signed (`timestamp` + `sign` folded into the body).

Like the Telegram channel, the text is sent as a **plain-text** message
(`msg_type: "text"`) on purpose: bodies are arbitrary agent output, so a
rich-text / card format would risk a parse error on stray markup. Plain
text never fails to render.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from lib.agent_messages.push import base
from lib.agent_messages.push.base import PushChannel, PushMessage

# Severity → leading glyph, so a Feishu notification is triageable at a
# glance. Mirrors the Telegram channel's map.
_GLYPH = {
    "progress": "•", "note": "•", "lesson": "✎", "result": "✓",
    "summary": "▣", "warning": "⚠", "blocker": "⛔",
}


class LarkChannel(PushChannel):
    channel_id = "lark"
    display_name = "Lark / Feishu"

    def is_configured(self) -> bool:
        return bool(self.cfg.lark_webhook_url)

    def min_severity(self) -> str:
        return self.cfg.lark_min_severity

    def _text(self, msg: PushMessage) -> str:
        glyph = _GLYPH.get(msg.msg_type or "", "•")
        head = f"{glyph} [{(msg.msg_type or 'progress').upper()}]"
        if msg.title:
            head += f" {msg.title}"
        lines = [head]
        if msg.body:
            lines.append(msg.body)
        for link in msg.links or []:
            label = link.get("label") or link.get("href")
            href = link.get("href")
            lines.append(f"• {label}: {href}" if label != href else f"• {href}")
        if msg.session_url:
            lines.append(f"↪ {msg.session_url}")
        return "\n".join(lines)

    def _sign(self, payload: dict) -> None:
        """Fold `timestamp` + `sign` into `payload` when a secret is set.

        Lark's scheme: HMAC-SHA256 over an *empty* message with the key
        `"{timestamp}\\n{secret}"`, base64-encoded. A no-op when the bot
        has signature verification disabled (`lark_secret` unset)."""
        secret = self.cfg.lark_secret
        if not secret:
            return
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{secret}"
        digest = hmac.new(
            string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
        payload["timestamp"] = timestamp
        payload["sign"] = base64.b64encode(digest).decode("utf-8")

    def deliver(self, msg: PushMessage) -> None:
        payload = {"msg_type": "text", "content": {"text": self._text(msg)}}
        self._sign(payload)
        base.http_post_json(self.cfg.lark_webhook_url, payload,
                            timeout=self.cfg.lark_timeout_seconds)


__all__ = ["LarkChannel"]
