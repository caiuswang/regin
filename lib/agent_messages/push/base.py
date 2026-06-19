"""Push-channel contract for outbound `send_to_user` delivery.

A *push channel* takes one agent→human message and delivers it
out-of-band (webhook, Telegram, …) so a long-running / background agent
reaches the user even when nobody is watching the inbox. Channels are
plugged in via `lib/agent_messages/push/registry.py`; the store fans a
message out to every configured channel whose severity gate it clears.

Adding a channel is a `PushChannel` subclass that implements
`is_configured` + `deliver`, plus one entry in the registry. Everything
else — severity gating, error capture, status aggregation — is handled
once in the registry, so a channel only has to know its own transport.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Optional

from lib.orm.models.agent_messages import severity_rank
from lib.settings import settings


@dataclass(frozen=True)
class PushMessage:
    """The provider-neutral view of one message handed to every channel.

    Built once per dispatch from the serialized `agent_messages` row, so
    each channel formats the *same* data its own way (JSON body, chat
    text, …) instead of re-deriving fields off the raw dict.
    """

    msg_type: Optional[str]
    title: Optional[str]
    body: Optional[str]
    links: Optional[list]
    session_id: str
    session_url: Optional[str]
    timestamp: Optional[str]


def build_push_message(msg: dict) -> PushMessage:
    """Project a serialized message row into a `PushMessage`."""
    trace_id = msg.get("trace_id") or ""
    base = settings.agent_messages.base_url.rstrip("/")
    return PushMessage(
        msg_type=msg.get("msg_type"),
        title=msg.get("title"),
        body=msg.get("body"),
        links=msg.get("links"),
        session_id=trace_id,
        session_url=_session_url(base, trace_id, msg.get("span_id")),
        timestamp=msg.get("created_at"),
    )


def _session_url(base: str, trace_id: str, span_id: Optional[str]) -> Optional[str]:
    """Deep-link to the originating span when known, else the session.

    Mirrors the in-app inbox card (`InboxMessageCard.sessionHref`): a
    `?span=` query is what `useTraceData` reads to focus/scroll to the exact
    span, so a permission-blocker push lands on the prompt it's about rather
    than the top of the session."""
    if not trace_id:
        return None
    url = f"{base}/trace/sessions/{trace_id}"
    return f"{url}?span={span_id}" if span_id else url


def http_post_json(url: str, payload: dict, *, timeout: float,
                   headers: Optional[dict] = None) -> None:
    """POST `payload` as JSON. Raises on transport/HTTP error.

    The single network seam shared by every channel — tests monkeypatch
    this one function instead of stubbing `urllib` per channel, and the
    registry's try/except turns a raised exception into a `failed` status.
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout):
        return None


class PushChannel:
    """Base adapter for one outbound delivery channel.

    Subclasses set `channel_id`/`display_name`, read their own config off
    `settings.agent_messages`, and implement `is_configured` + `deliver`.
    `deliver` should raise on failure — the registry records the outcome;
    a channel never swallows its own errors or returns a status string.
    """

    channel_id: str = "unknown"
    display_name: str = "Unknown"

    @property
    def cfg(self):
        return settings.agent_messages

    def is_configured(self) -> bool:
        """True when this channel has enough config to attempt delivery."""
        raise NotImplementedError

    def min_severity(self) -> str:
        """Effective severity gate for this channel (a `MESSAGE_TYPES` value)."""
        raise NotImplementedError

    def clears_gate(self, msg_type: Optional[str]) -> bool:
        return severity_rank(msg_type) >= severity_rank(self.min_severity())

    def deliver(self, msg: PushMessage) -> None:
        """Send `msg`. Raise on any failure; return value is ignored."""
        raise NotImplementedError


__all__ = ["PushMessage", "PushChannel", "build_push_message", "http_post_json"]
