"""Back-compat shim — outbound push moved to `lib/agent_messages/push/`.

The single-webhook dispatcher this module used to hold is now one channel
(`push/webhook.py`) behind a fan-out registry that also drives Telegram
and any future channel. Kept so existing imports of
`agent_messages.webhook.{maybe_dispatch,should_dispatch}` keep working;
new code should import `lib.agent_messages.push.registry` directly.
"""

from __future__ import annotations

from lib.agent_messages.push.registry import maybe_dispatch, should_dispatch

__all__ = ["maybe_dispatch", "should_dispatch"]
