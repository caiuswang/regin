"""The /live composer against a session regin owns (`web/blueprints/bridge`).

The composer posts to `bridge-send` regardless of which tier owns the session.
For a tmux-observed session that types into a pane; for an SDK-owned one it has
to queue a prompt instead — and must not be gated on `agent_bridge.enabled`,
which authorizes keystroke injection into someone else's terminal, not talking
to a process regin started itself. Same reasoning as `bridge-answer`.
"""

from __future__ import annotations

import pytest

from lib import agent_sdk
from lib.agent_bridge import delivery, store
from lib.settings import settings


@pytest.fixture
def sdk_owned(monkeypatch):
    sent = {}

    def _submit(trace_id, text):
        sent["call"] = (trace_id, text)
        return True, "prompt queued"

    monkeypatch.setattr(agent_sdk, "is_sdk_owned",
                        lambda trace_id: trace_id.startswith("sdk-"))
    monkeypatch.setattr(agent_sdk, "submit_prompt", _submit)
    return sent


@pytest.fixture
def no_tmux(monkeypatch):
    """Fail loudly if the tmux transport is reached for an owned session."""
    def _deliver(*args, **kwargs):
        raise AssertionError("tmux delivery must not run for an SDK session")

    monkeypatch.setattr(delivery, "deliver", _deliver)


def test_composer_message_is_queued_on_the_owned_run(flask_client, sdk_owned,
                                                     no_tmux, monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)

    body = flask_client.post("/api/sessions/sdk-abc/bridge-send",
                             json={"text": "keep going"}).get_json()

    assert body["delivered"] is True
    assert body["detail"] == "prompt queued"
    assert sdk_owned["call"] == ("sdk-abc", "keep going")


def test_the_message_is_still_recorded_in_the_steering_inbox(flask_client,
                                                             sdk_owned, no_tmux):
    body = flask_client.post("/api/sessions/sdk-inbox/bridge-send",
                             json={"text": "audit me"}).get_json()

    row = next(m for m in store.list_bridge_messages(session_id="sdk-inbox")
               if m["id"] == body["id"])
    assert row["body"] == "audit me"
    assert row["delivered"] == 1


def test_a_multiline_prompt_reaches_the_agent_intact(flask_client, sdk_owned,
                                                     no_tmux):
    """`sanitize_text` flattens newlines so tmux can't submit a half-typed
    message — a constraint of typing, not of a queue."""
    flask_client.post("/api/sessions/sdk-multi/bridge-send",
                      json={"text": "fix this:\n\n  File a.py, line 3\n"})

    assert sdk_owned["call"][1] == "fix this:\n\n  File a.py, line 3"


def test_control_bytes_are_still_stripped_from_an_sdk_prompt(flask_client,
                                                             sdk_owned,
                                                             no_tmux):
    flask_client.post("/api/sessions/sdk-ctrl/bridge-send",
                      json={"text": "clean\x07this\x1b[31m up"})

    assert sdk_owned["call"][1] == "cleanthis[31m up"


def test_empty_text_is_rejected_before_any_transport(flask_client, sdk_owned,
                                                     no_tmux):
    assert flask_client.post("/api/sessions/sdk-abc/bridge-send",
                             json={"text": "  "}).status_code == 400
    assert "call" not in sdk_owned


def test_a_non_sdk_session_still_refuses_when_the_bridge_is_off(
        flask_client, sdk_owned, monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)

    body = flask_client.post("/api/sessions/tmux-xyz/bridge-send",
                             json={"text": "hi"}).get_json()

    assert body == {"delivered": False, "detail": "bridge disabled"}
