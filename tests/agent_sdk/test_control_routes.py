"""Follow-up prompts and the control plane (`web/blueprints/agent_runs`).

Launching returns a trace id; these routes are what you can do with it
afterwards. Every refusal here is structured rather than an exception: the
caller is usually a phone holding a card for a session that may have ended
between the poll and the tap.
"""

from __future__ import annotations

import pytest

from lib import agent_sdk
from lib.agent_sdk import registry, store


@pytest.fixture
def owned(monkeypatch):
    """A run this process claims to own, with its control calls stubbed."""
    calls = {}

    def _prompt(trace_id, text):
        calls["prompt"] = (trace_id, text)
        return True, "prompt queued"

    def _stop(trace_id):
        calls["stop"] = trace_id
        return True, "stopping"

    def _interrupt(trace_id):
        calls["interrupt"] = trace_id
        return True, "interrupt sent"

    def _edit(trace_id, prompt_id, text):
        calls["edit"] = (trace_id, prompt_id, text)
        return True, "prompt updated"

    def _cancel(trace_id, prompt_id):
        calls["cancel"] = (trace_id, prompt_id)
        return True, "prompt removed"

    monkeypatch.setattr(registry, "submit_prompt", _prompt)
    monkeypatch.setattr(agent_sdk, "stop_run", _stop)
    monkeypatch.setattr(agent_sdk, "interrupt_run", _interrupt)
    monkeypatch.setattr(agent_sdk, "edit_queued", _edit)
    monkeypatch.setattr(agent_sdk, "cancel_queued", _cancel)
    return calls


def test_prompt_is_queued_onto_the_owned_run(flask_client, owned):
    res = flask_client.post("/api/agent-runs/sdk-abc/prompt",
                            json={"prompt": "and now the tests"})

    assert res.status_code == 200
    assert res.get_json() == {"queued": True, "detail": "prompt queued"}
    assert owned["prompt"] == ("sdk-abc", "and now the tests")


def test_empty_prompt_is_rejected(flask_client, owned):
    assert flask_client.post("/api/agent-runs/sdk-abc/prompt",
                             json={"prompt": "  "}).status_code == 400
    assert flask_client.post("/api/agent-runs/sdk-abc/prompt",
                             json={}).status_code == 400


def test_prompt_is_bounded(flask_client, owned):
    flask_client.post("/api/agent-runs/sdk-abc/prompt",
                      json={"prompt": "x" * 20000})

    assert len(owned["prompt"][1]) == 8000


def test_prompting_a_dead_run_is_a_structured_refusal(flask_client):
    res = flask_client.post("/api/agent-runs/sdk-gone/prompt",
                            json={"prompt": "hello?"})

    assert res.status_code == 200
    assert res.get_json() == {"queued": False,
                              "detail": "no live agent session"}


def test_stop_and_interrupt_reach_the_registry(flask_client, owned):
    stop = flask_client.post("/api/agent-runs/sdk-abc/stop").get_json()
    interrupt = flask_client.post("/api/agent-runs/sdk-abc/interrupt").get_json()

    assert stop == {"delivered": True, "detail": "stopping"}
    assert interrupt == {"delivered": True, "detail": "interrupt sent"}
    assert owned["stop"] == "sdk-abc"
    assert owned["interrupt"] == "sdk-abc"


def test_control_of_an_unowned_run_refuses_rather_than_500s(flask_client):
    for path in ("stop", "interrupt"):
        body = flask_client.post(f"/api/agent-runs/sdk-gone/{path}").get_json()

        assert body["delivered"] is False
        assert body["detail"] == "no live agent session"


def test_a_queued_prompt_can_be_rewritten_in_place(flask_client, owned):
    res = flask_client.patch("/api/agent-runs/sdk-abc/queue/q2",
                             json={"prompt": "and now the tests, properly"})

    assert res.status_code == 200
    assert res.get_json() == {"updated": True, "detail": "prompt updated"}
    assert owned["edit"] == ("sdk-abc", "q2", "and now the tests, properly")


def test_an_edit_is_bounded_and_needs_a_prompt(flask_client, owned):
    assert flask_client.patch("/api/agent-runs/sdk-abc/queue/q1",
                              json={"prompt": " "}).status_code == 400
    assert flask_client.patch("/api/agent-runs/sdk-abc/queue/q1",
                              json={}).status_code == 400

    flask_client.patch("/api/agent-runs/sdk-abc/queue/q1",
                       json={"prompt": "x" * 20000})
    assert len(owned["edit"][2]) == 8000


def test_a_queued_prompt_can_be_removed(flask_client, owned):
    res = flask_client.delete("/api/agent-runs/sdk-abc/queue/q3")

    assert res.status_code == 200
    assert res.get_json() == {"removed": True, "detail": "prompt removed"}
    assert owned["cancel"] == ("sdk-abc", "q3")


def test_queue_mutations_on_an_unowned_run_refuse_rather_than_500(flask_client):
    """The card is a phone, and the run it is showing may have ended between
    the poll that rendered the row and the tap on it."""
    edited = flask_client.patch("/api/agent-runs/sdk-gone/queue/q1",
                                json={"prompt": "too late"}).get_json()
    removed = flask_client.delete(
        "/api/agent-runs/sdk-gone/queue/q1").get_json()

    assert edited == {"updated": False, "detail": "no live agent session"}
    assert removed == {"removed": False, "detail": "no live agent session"}


def test_reaper_closes_runs_no_process_backs(flask_client):
    store.upsert_run("sdk-orphan", status="running")
    try:
        closed = store.reap_orphaned_runs()

        assert closed >= 1
        row = store.get_run("sdk-orphan")
        assert row["status"] == "exited"
        assert row["detail"] == "server restarted"
    finally:
        registry.unregister_run("sdk-orphan")


def test_reaper_does_not_fabricate_reachability(flask_client):
    store.upsert_run("sdk-orphan2", status="running")
    store.reap_orphaned_runs()

    assert agent_sdk.is_sdk_owned("sdk-orphan2") is False
