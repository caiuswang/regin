"""The suite must never reach a real push channel.

`store.record_message` fans out through `push.registry.maybe_dispatch`,
which reads the live `settings.agent_messages` — so on a machine with a
configured Feishu/Telegram/webhook bot, unrelated tests delivered real
chat messages to the developer. `conftest._block_push_transport` closes
that; these are its guard.

The guard lives in this testpath, not under `tests/`, on purpose.
`tests/conftest.py::_default_feature_config` already resets
`agent_messages` for everything below it, so a copy of these assertions
there would pass with the root fixture deleted — it would be guarding the
wrong fixture. `hook_manager/tests` has no such parent, and is where every
one of the observed real pushes came from.
"""

from __future__ import annotations

import pytest

from lib.agent_messages import store
from lib.agent_messages.push import base, registry
from lib.settings import settings

# Captured at import, before the autouse fixture patches the seam, so a test
# can tell the stub from the real POST by identity rather than by behavior.
_REAL_POST_JSON = base.http_post_json


def test_no_channel_is_configured_under_test():
    """The developer's `config/settings.json` must not reach the suite."""
    assert registry.configured_channels() == []
    assert registry.active_channels() == []


@pytest.mark.parametrize("msg_type", ["result", "warning", "blocker", "summary"])
def test_recording_a_high_severity_message_dispatches_nothing(msg_type):
    """A type that clears every default gate still leaves no channel to take it."""
    data = store.record_message(
        trace_id="push-isolation-fixture",
        body=f"a {msg_type} that must not be delivered",
        msg_type=msg_type,
    )
    assert data["webhook_status"] is None


def test_a_configured_channel_still_cannot_reach_the_network(monkeypatch):
    """Layer 2: configuring a channel by hand does not re-open the socket.

    Several tests set their own webhook URL; without the severed seam they
    would escape the pristine-defaults reset of layer 1.
    """
    monkeypatch.setattr(settings.agent_messages, "webhook_url", "http://hook.test/x")
    monkeypatch.setattr(settings.agent_messages, "webhook_min_severity", "progress")
    assert registry.active_channels(), "the hand-configured channel should be active"

    assert base.http_post_json is not _REAL_POST_JSON
    data = store.record_message(
        trace_id="push-isolation-fixture",
        body="delivered to the stub, not to a socket",
        msg_type="blocker",
    )
    assert data["webhook_status"] == "sent"
