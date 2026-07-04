"""Unit tests for the pluggable push channels (lib.agent_messages.push).

Covers the fan-out registry, per-channel severity gating, status
aggregation, and each channel's payload shape. The single network seam
(`base.http_post_json`) is monkeypatched so nothing leaves the process.
"""

from __future__ import annotations

import pytest

from lib.agent_messages.push import base, registry
from lib.agent_messages.push.lark import LarkChannel
from lib.agent_messages.push.telegram import TelegramChannel
from lib.agent_messages.push.webhook import WebhookChannel
from lib.settings import settings


@pytest.fixture(autouse=True)
def _clean_channels(monkeypatch):
    """Start every test from a no-channels-configured baseline.

    `settings` loads real config (including a developer's local Telegram
    token in `settings.local.json`), which would otherwise make the
    "nothing configured" assertions fan out for real. Clear the keys each
    channel's `is_configured` reads; tests that need a channel set it back
    via `_cfg`."""
    for key in ("webhook_url", "telegram_bot_token", "telegram_chat_id",
                "lark_webhook_url", "lark_secret"):
        monkeypatch.setattr(settings.agent_messages, key, None)


@pytest.fixture
def captured(monkeypatch):
    """Capture every outbound POST instead of sending it; (url, payload)."""
    calls: list[tuple[str, dict]] = []

    def _fake(url, payload, *, timeout, headers=None):
        calls.append((url, payload))

    monkeypatch.setattr(base, "http_post_json", _fake)
    return calls


def _cfg(monkeypatch, **kw):
    for key, val in kw.items():
        monkeypatch.setattr(settings.agent_messages, key, val)


def _msg(msg_type="blocker", **kw):
    base_msg = {"trace_id": "s1", "msg_type": msg_type, "title": "T",
                "body": "b", "links": None, "created_at": "now"}
    base_msg.update(kw)
    return base_msg


def test_no_channels_configured_returns_none(captured):
    # default settings: no webhook_url, no telegram token
    assert registry.maybe_dispatch(_msg()) is None
    assert captured == []


def test_webhook_only_sends_json_payload(monkeypatch, captured):
    _cfg(monkeypatch, webhook_url="http://hook.test/x")
    assert registry.maybe_dispatch(_msg()) == "sent"
    assert len(captured) == 1
    url, payload = captured[0]
    assert url == "http://hook.test/x"
    assert payload["event"] == "agent_message"
    assert payload["type"] == "blocker"
    assert payload["session_url"].endswith("/trace/sessions/s1")


def test_webhook_session_url_deep_links_to_span(monkeypatch, captured):
    # A message tied to a span (e.g. a permission blocker) deep-links to it,
    # mirroring the in-app inbox card so the link lands on the prompt.
    _cfg(monkeypatch, webhook_url="http://hook.test/x")
    registry.maybe_dispatch(_msg(span_id="tu_42"))
    payload = next(p for _, p in captured if "session_url" in p)
    assert payload["session_url"].endswith("/trace/sessions/s1?span=tu_42")


def test_webhook_omits_session_url_for_non_session_trace(monkeypatch, captured):
    """A content-drift card lives under the synthetic `wiki-debt` trace, which
    is not a navigable session — the push must not render a dead
    /trace/sessions/wiki-debt link (regression for the reported bad URL)."""
    _cfg(monkeypatch, webhook_url="http://hook.test/x")
    registry.maybe_dispatch(_msg(trace_id="wiki-debt"))
    payload = next(p for _, p in captured if p.get("event") == "agent_message")
    assert payload["session_url"] is None


def test_push_resolves_relative_link_hrefs_to_absolute_urls(monkeypatch, captured):
    """App-relative link hrefs (`/repos/…`) are absolutized against base_url so
    a Feishu/Telegram/webhook reader can actually click them — a bare
    `/repos/regin/topics` is not a working link outside the SPA."""
    _cfg(monkeypatch, webhook_url="http://hook.test/x",
         lark_webhook_url="https://open.feishu.cn/hook/abc",
         base_url="https://regin.example")
    links = [{"label": "Review in Topics", "href": "/repos/regin/topics"}]
    registry.maybe_dispatch(_msg(links=links))
    webhook = next(p for _, p in captured if p.get("event") == "agent_message")
    assert webhook["links"] == [
        {"label": "Review in Topics",
         "href": "https://regin.example/repos/regin/topics"}]
    lark = next(p for _, p in captured if p.get("msg_type") == "text")
    assert "https://regin.example/repos/regin/topics" in lark["content"]["text"]


def test_telegram_only_sends_bot_api_text(monkeypatch, captured):
    _cfg(monkeypatch, telegram_bot_token="BOT:1", telegram_chat_id="42")
    assert registry.maybe_dispatch(_msg(title="Down", body="db gone")) == "sent"
    url, payload = captured[0]
    assert url == "https://api.telegram.org/botBOT:1/sendMessage"
    assert payload["chat_id"] == "42"
    assert "[BLOCKER] Down" in payload["text"]
    assert "db gone" in payload["text"]


def test_lark_only_sends_custom_bot_text(monkeypatch, captured):
    _cfg(monkeypatch, lark_webhook_url="https://open.feishu.cn/hook/abc")
    assert registry.maybe_dispatch(_msg(title="Down", body="db gone")) == "sent"
    url, payload = captured[0]
    assert url == "https://open.feishu.cn/hook/abc"
    assert payload["msg_type"] == "text"
    assert "[BLOCKER] Down" in payload["content"]["text"]
    assert "db gone" in payload["content"]["text"]
    # unsigned when no secret is configured
    assert "sign" not in payload and "timestamp" not in payload


def test_lark_signs_when_secret_set(monkeypatch, captured):
    _cfg(monkeypatch, lark_webhook_url="https://open.feishu.cn/hook/abc",
         lark_secret="s3cr3t")
    assert registry.maybe_dispatch(_msg()) == "sent"
    _, payload = captured[0]
    # timestamp + sign folded into the body, and the sign is derived from
    # that exact timestamp per Lark's HMAC-SHA256(empty, "{ts}\n{secret}").
    assert payload["timestamp"] and payload["sign"]
    import base64 as _b64
    import hashlib
    import hmac
    expected = _b64.b64encode(hmac.new(
        f"{payload['timestamp']}\ns3cr3t".encode(), b"",
        hashlib.sha256).digest()).decode()
    assert payload["sign"] == expected


def test_fans_out_to_every_configured_channel(monkeypatch, captured):
    _cfg(monkeypatch, webhook_url="http://hook.test/x",
         telegram_bot_token="BOT:1", telegram_chat_id="42")
    assert registry.maybe_dispatch(_msg()) == "sent"
    assert len(captured) == 2
    assert {c.channel_id for c in registry.configured_channels()} == {
        "webhook", "telegram"}


def test_per_channel_severity_gate(monkeypatch, captured):
    # webhook gates at 'note'; telegram only at 'blocker'.
    _cfg(monkeypatch, webhook_url="http://hook.test/x",
         webhook_min_severity="note",
         telegram_bot_token="BOT:1", telegram_chat_id="42",
         telegram_min_severity="blocker")
    assert registry.maybe_dispatch(_msg(msg_type="warning")) == "sent"
    # only the webhook cleared its gate
    assert len(captured) == 1
    assert captured[0][0] == "http://hook.test/x"


def test_all_gated_out_returns_skipped(monkeypatch, captured):
    _cfg(monkeypatch, webhook_url="http://hook.test/x",
         webhook_min_severity="blocker")
    assert registry.maybe_dispatch(_msg(msg_type="progress")) == "skipped"
    assert captured == []


def test_transport_failure_is_caught_and_aggregated(monkeypatch):
    _cfg(monkeypatch, webhook_url="http://hook.test/x")

    def _boom(url, payload, *, timeout, headers=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(base, "http_post_json", _boom)
    # never raises; surfaces as a 'failed' status for the row
    assert registry.maybe_dispatch(_msg()) == "failed"


def test_one_failure_one_success_aggregates_sent(monkeypatch):
    _cfg(monkeypatch, webhook_url="http://hook.test/x",
         telegram_bot_token="BOT:1", telegram_chat_id="42")
    seen: list[str] = []

    def _half(url, payload, *, timeout, headers=None):
        if "telegram" in url:
            raise RuntimeError("tg down")
        seen.append(url)

    monkeypatch.setattr(base, "http_post_json", _half)
    # webhook sent, telegram failed → aggregate is 'sent'
    assert registry.maybe_dispatch(_msg()) == "sent"
    assert seen == ["http://hook.test/x"]


def test_should_dispatch_reflects_gates(monkeypatch):
    assert registry.should_dispatch("blocker") is False  # nothing configured
    _cfg(monkeypatch, webhook_url="http://hook.test/x",
         webhook_min_severity="warning")
    assert registry.should_dispatch("blocker") is True
    assert registry.should_dispatch("progress") is False


def test_channel_classes_implement_contract():
    for cls in (WebhookChannel, TelegramChannel, LarkChannel):
        c = cls()
        assert c.channel_id != "unknown"
        assert c.display_name
        assert isinstance(c.is_configured(), bool)
