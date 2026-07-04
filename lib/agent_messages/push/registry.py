"""Push-channel registry + fan-out dispatcher.

Holds the list of known channels and the single `maybe_dispatch` entry
point the store calls after persisting a message. A message is delivered
to *every* configured channel whose per-channel severity gate it clears;
each channel's outcome is captured independently so one failing transport
never blocks the others.

**Register a new channel here** — append its class to `_CHANNEL_CLASSES`.
That is the only wiring step beyond the `PushChannel` subclass itself.
"""

from __future__ import annotations

from lib.activity_log import get_activity_logger
from lib.agent_messages.push.base import PushChannel, build_push_message
from lib.agent_messages.push.lark import LarkChannel
from lib.agent_messages.push.telegram import TelegramChannel
from lib.agent_messages.push.webhook import WebhookChannel

log = get_activity_logger("agent_messages")

# Ordered registry of every known push channel. Append to extend.
_CHANNEL_CLASSES: tuple[type[PushChannel], ...] = (
    WebhookChannel,
    TelegramChannel,
    LarkChannel,
)


def all_channels() -> list[PushChannel]:
    """One instance of every registered channel (configured or not)."""
    return [cls() for cls in _CHANNEL_CLASSES]


def configured_channels() -> list[PushChannel]:
    """Channels with enough config to attempt delivery."""
    return [c for c in all_channels() if c.is_configured()]


def should_dispatch(msg_type: str | None) -> bool:
    """True when at least one configured channel would deliver this type."""
    return any(c.clears_gate(msg_type) for c in configured_channels())


def _aggregate(statuses: list[str]) -> str:
    """Collapse per-channel outcomes into one column value for the row.

    'sent' if anything went out, else 'failed' if anything tried and
    failed, else 'skipped' (configured channels all gated out by severity).
    """
    if "sent" in statuses:
        return "sent"
    if "failed" in statuses:
        return "failed"
    return "skipped"


def maybe_dispatch(msg: dict) -> str | None:
    """Deliver `msg` to every configured channel that clears its gate.

    Returns an aggregate status ('sent' | 'failed' | 'skipped') for
    `agent_messages.webhook_status`, or None when no channel is configured
    at all (so the caller leaves the column NULL). Never raises — each
    channel's transport error is caught and logged per channel.
    """
    channels = configured_channels()
    if not channels:
        return None
    pm = build_push_message(msg)
    statuses: list[str] = []
    for channel in channels:
        if not channel.clears_gate(pm.msg_type):
            statuses.append("skipped")
            continue
        try:
            channel.deliver(pm)
            statuses.append("sent")
            log.write("push_sent", channel=channel.channel_id,
                      msg_type=pm.msg_type, trace_id=pm.session_id)
        except Exception:
            statuses.append("failed")
            log.error("push_failed", channel=channel.channel_id, exc_info=True)
    return _aggregate(statuses)


__all__ = ["maybe_dispatch", "should_dispatch", "all_channels",
           "configured_channels"]
