"""Pluggable push channels for `send_to_user` outbound delivery.

`registry.maybe_dispatch` fans a persisted agent→human message out to
every configured `PushChannel` (webhook, Telegram, …) whose severity gate
it clears. The store calls it as the single push entry point.

  * `base`     — the `PushChannel` contract + `PushMessage` payload.
  * `registry` — channel list + `maybe_dispatch` fan-out.
  * `webhook`  — generic JSON-POST channel.
  * `telegram` — Telegram Bot API channel.
"""

from __future__ import annotations

__all__ = ["base", "registry", "webhook", "telegram"]
