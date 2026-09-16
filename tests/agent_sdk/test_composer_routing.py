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
from lib.agent_bridge import commands, delivery, store
from lib.settings import settings


@pytest.fixture
def sdk_owned(monkeypatch):
    sent = {}

    def _submit(trace_id, text, images=None, row_id=None):
        sent["call"] = (trace_id, text)
        sent["row_id"] = row_id
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
    """A queue has no composer to overflow, so it gets its own larger cap —
    `_SDK_PROMPT_MAX` — rather than the tmux path's `max_text_len`."""
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


@pytest.fixture
def catalog(monkeypatch):
    """Stand in for the SDK handshake: `names` is what the target session's
    own catalog offers, `source` which leg produced it."""
    state = {"names": ["clear", "usage", "compact"], "source": "sdk"}

    def _rows(trace_id):
        return ([{"name": n, "aliases": []} for n in state["names"]],
                state["source"])

    monkeypatch.setattr(commands, "_session_rows", _rows)
    return state


@pytest.fixture
def stopper(monkeypatch):
    calls = []

    def _stop(trace_id):
        calls.append(trace_id)
        return True, "stopping"

    monkeypatch.setattr(agent_sdk, "stop_run", _stop)
    return calls


@pytest.mark.parametrize("verb", ["/exit", "/quit"])
def test_a_quit_verb_ends_the_run_instead_of_being_queued(
        verb, flask_client, sdk_owned, no_tmux, catalog, stopper):
    """The reported bug: no catalog offers `/exit`, so the CLI answered it with
    a synthetic refusal while regin acked 'prompt queued' and the run lived on.
    """
    body = flask_client.post("/api/sessions/sdk-quit/bridge-send",
                             json={"text": verb}).get_json()

    assert body["delivered"] is True
    assert body["detail"] == "stopping"
    assert stopper == ["sdk-quit"]
    assert "call" not in sdk_owned


def test_a_refused_stop_is_reported_rather_than_acked(
        flask_client, sdk_owned, no_tmux, catalog, monkeypatch):
    monkeypatch.setattr(agent_sdk, "stop_run",
                        lambda trace_id: (False, "no live agent session"))

    body = flask_client.post("/api/sessions/sdk-dead/bridge-send",
                             json={"text": "/exit"}).get_json()

    assert body["delivered"] is False
    assert "no live agent session" in body["detail"]


def test_a_command_the_catalog_does_not_offer_is_refused_in_the_composer(
        flask_client, sdk_owned, no_tmux, catalog):
    body = flask_client.post("/api/sessions/sdk-nope/bridge-send",
                             json={"text": "/logout"}).get_json()

    assert body["delivered"] is False
    assert "/logout" in body["detail"]
    assert "call" not in sdk_owned


def test_a_command_the_catalog_offers_is_still_forwarded(
        flask_client, sdk_owned, no_tmux, catalog):
    body = flask_client.post("/api/sessions/sdk-clear/bridge-send",
                             json={"text": "/clear"}).get_json()

    assert body["delivered"] is True
    assert sdk_owned["call"] == ("sdk-clear", "/clear")


def test_an_alias_counts_as_offered(flask_client, sdk_owned, no_tmux,
                                    catalog, monkeypatch):
    monkeypatch.setattr(
        commands, "_session_rows",
        lambda t: ([{"name": "clear", "aliases": ["new"]}], "sdk"))

    body = flask_client.post("/api/sessions/sdk-alias/bridge-send",
                             json={"text": "/new"}).get_json()

    assert body["delivered"] is True
    assert sdk_owned["call"] == ("sdk-alias", "/new")


def test_a_degraded_catalog_forwards_rather_than_refusing(
        flask_client, sdk_owned, no_tmux, catalog):
    """The filesystem scan cannot see built-ins, so refusing against it would
    reject every command the SDK alone knows about. Fail open."""
    catalog["source"] = "scan"

    body = flask_client.post("/api/sessions/sdk-degraded/bridge-send",
                             json={"text": "/anything-at-all"}).get_json()

    assert body["delivered"] is True
    assert sdk_owned["call"] == ("sdk-degraded", "/anything-at-all")


def test_a_plain_prompt_never_consults_the_catalog(flask_client, sdk_owned,
                                                   no_tmux, monkeypatch):
    def _boom(trace_id):
        raise AssertionError("a non-slash prompt must not handshake")

    monkeypatch.setattr(commands, "_session_rows", _boom)

    body = flask_client.post("/api/sessions/sdk-plain/bridge-send",
                             json={"text": "carry on"}).get_json()

    assert body["delivered"] is True
    assert sdk_owned["call"] == ("sdk-plain", "carry on")


def test_a_tmux_session_still_types_its_own_quit_verb(flask_client, catalog,
                                                      stopper, monkeypatch):
    """Only the SDK tier lacks a TUI. A pane runs `/exit` for real, and this
    routing must not reach across to it."""
    typed = []

    def _deliver(trace_id, text, as_command=False):
        typed.append((trace_id, text, as_command))
        return delivery.DeliveryResult(True, "delivered to %0")

    monkeypatch.setattr(agent_sdk, "is_sdk_owned", lambda trace_id: False)
    monkeypatch.setattr(settings.agent_bridge, "enabled", True)
    monkeypatch.setattr(delivery, "deliver", _deliver)

    body = flask_client.post("/api/sessions/tmux-1/bridge-send",
                             json={"text": "/exit"}).get_json()

    assert body["delivered"] is True
    assert typed == [("tmux-1", "/exit", True)]
    assert stopper == []


@pytest.mark.parametrize("text", [
    "/Users/taowang/regin/lib/x.py is broken",
    "/api/sessions/abc/map returns 500",
    "/tmp/log.txt has the trace",
])
def test_a_prompt_opening_with_a_path_is_not_a_command(
        text, flask_client, sdk_owned, no_tmux, catalog):
    """A leading slash TOKEN is a command; a path is not. Without the
    terminator on the name pattern these prose prompts — routine in this repo —
    were refused as an unknown `/Users`, `/api`, `/tmp`."""
    body = flask_client.post("/api/sessions/sdk-path/bridge-send",
                             json={"text": text}).get_json()

    assert body["delivered"] is True
    assert sdk_owned["call"] == ("sdk-path", text)


@pytest.mark.parametrize("verb", ["/EXIT", "/Quit"])
def test_a_capitalized_quit_verb_still_ends_the_run(
        verb, flask_client, sdk_owned, no_tmux, catalog, stopper):
    body = flask_client.post("/api/sessions/sdk-caps/bridge-send",
                             json={"text": verb}).get_json()

    assert body["delivered"] is True
    assert stopper == ["sdk-caps"]


def test_the_catalog_itself_is_matched_exactly(flask_client, sdk_owned,
                                               no_tmux, catalog):
    """Only the quit verbs are case-folded: a command's name in the catalog is
    its name, and folding there would invent commands the CLI does not have."""
    body = flask_client.post("/api/sessions/sdk-case/bridge-send",
                             json={"text": "/CLEAR"}).get_json()

    assert body["delivered"] is False
