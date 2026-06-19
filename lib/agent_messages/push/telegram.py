"""Telegram push channel (Bot API `sendMessage`).

Delivers a message to a Telegram chat so you can read agent progress from
your phone. Configure `telegram_bot_token` (from @BotFather) and
`telegram_chat_id` (your user/group id) under `settings.agent_messages`.

The text is sent as **plain text** (no `parse_mode`) on purpose: message
bodies are arbitrary agent output and Telegram's Markdown/HTML parsers
reject unescaped `_ * [ <` etc., which would turn a delivery into a 400.
Plain text never fails to render.
"""

from __future__ import annotations

from lib.agent_messages.push import base
from lib.agent_messages.push.base import PushChannel, PushMessage

# Severity → leading glyph, so a phone notification is triageable at a
# glance. Falls back to a neutral dot for unknown/low types.
_GLYPH = {
    "progress": "•", "note": "•", "lesson": "✎", "result": "✓",
    "summary": "▣", "warning": "⚠", "blocker": "⛔",
}


class TelegramChannel(PushChannel):
    channel_id = "telegram"
    display_name = "Telegram"

    def is_configured(self) -> bool:
        return bool(self.cfg.telegram_bot_token and self.cfg.telegram_chat_id)

    def min_severity(self) -> str:
        return self.cfg.telegram_min_severity

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

    def deliver(self, msg: PushMessage) -> None:
        url = f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}/sendMessage"
        base.http_post_json(
            url,
            {"chat_id": self.cfg.telegram_chat_id, "text": self._text(msg),
             "disable_web_page_preview": True},
            timeout=self.cfg.telegram_timeout_seconds,
        )


__all__ = ["TelegramChannel"]
