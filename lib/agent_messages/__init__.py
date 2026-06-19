"""Agent → human message channel (the `send_to_user` inbox).

The durable store + optional webhook behind regin's `send_to_user` MCP
tool. An agent calls the tool mid-task; the PostToolUse hook records a
typed, supersedable message here; the web UI renders a per-session feed
plus a cross-session inbox with an unread badge.

  * `store` — CRUD (record / list / inbox / read / ack / dismiss).
  * `push`  — pluggable outbound channels (webhook, Telegram, …) that a
    high-severity message fans out to; see `push/registry.py`.

Submodules are imported directly (`from lib.agent_messages import store`)
to keep this package import side-effect free.
"""

from __future__ import annotations

__all__ = ["store", "push"]
