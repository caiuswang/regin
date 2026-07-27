"""Transport routing for `POST /api/sessions/<id>/bridge-answer`.

Two tiers answer the same question through the same endpoint and the same
frontend payload. These tests pin the routing contract: a session regin owns is
answered over its typed channel and never touches tmux, and — the part that is
easy to regress — it does so without requiring `agent_bridge.enabled`, because
that flag authorizes keystroke injection into a terminal, which the typed path
never performs.
"""

from __future__ import annotations

import pytest

from lib import agent_sdk
from lib.agent_bridge import delivery
from lib.agent_sdk import registry
from lib.settings import settings

_TRACE = "sdk-owned-session"


@pytest.fixture
def sdk_session():
    registry.register_run(_TRACE, object())
    yield _TRACE
    registry.unregister_run(_TRACE)


@pytest.fixture
def no_tmux(monkeypatch):
    """Fail loudly if anything reaches the keystroke transport."""
    def _boom(*args, **kwargs):
        raise AssertionError("tmux delivery must not run for an SDK session")

    monkeypatch.setattr(delivery, "deliver_answer", _boom)
    monkeypatch.setattr(delivery, "deliver_answers", _boom)


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def _resolve(trace_id, answers):
        seen["trace_id"] = trace_id
        seen["answers"] = answers
        return True, "answer delivered"

    # The blueprint calls `agent_sdk.resolve_ask`, which the package re-exports
    # by value at import time — patching `registry.resolve_ask` would not be
    # seen by the route.
    monkeypatch.setattr(agent_sdk, "resolve_ask", _resolve)
    return seen


def test_multi_question_answer_goes_to_the_typed_channel(
        flask_client, sdk_session, no_tmux, captured, monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    payload = {"answers": [{"option_index": 0, "label": "A"},
                           {"option_index": 1, "label": "B"}]}

    res = flask_client.post(
        f"/api/sessions/{_TRACE}/bridge-answer", json=payload)

    assert res.status_code == 200
    assert res.get_json()["delivered"] is True
    assert captured["trace_id"] == _TRACE
    assert captured["answers"] == payload["answers"]


def test_single_question_payload_is_normalized_to_a_list(
        flask_client, sdk_session, no_tmux, captured, monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)

    res = flask_client.post(f"/api/sessions/{_TRACE}/bridge-answer",
                            json={"option_index": 2, "label": "C"})

    assert res.status_code == 200
    assert captured["answers"] == [{"option_index": 2, "label": "C"}]


def test_typed_path_does_not_require_the_tmux_bridge_flag(
        flask_client, sdk_session, no_tmux, captured, monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)

    res = flask_client.post(f"/api/sessions/{_TRACE}/bridge-answer",
                            json={"option_index": 0})

    assert res.get_json()["detail"] != "bridge disabled"


def test_answer_without_an_option_index_is_rejected(
        flask_client, sdk_session, no_tmux):
    res = flask_client.post(f"/api/sessions/{_TRACE}/bridge-answer",
                            json={"nonsense": True})

    assert res.status_code == 400


def test_unowned_session_still_falls_through_to_the_tmux_path(
        flask_client, monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)

    res = flask_client.post("/api/sessions/some-terminal-session/bridge-answer",
                            json={"option_index": 0})

    assert res.get_json()["detail"] == "bridge disabled"


def test_sdk_session_reads_as_reachable_without_a_tmux_pane(sdk_session,
                                                            monkeypatch):
    """The /live sheet gates its answer UI on reachability. An SDK-owned
    session has no pane, so gating on one would render its own questions
    unanswerable."""
    from web.blueprints.trace.sessions import _bridge_reachability

    monkeypatch.setattr(settings.agent_bridge, "enabled", False)

    assert _bridge_reachability(_TRACE) == {"bridge_reachable": True,
                                            "bridge_pane": None}
    assert _bridge_reachability("not-ours")["bridge_reachable"] is False
