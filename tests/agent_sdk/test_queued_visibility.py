"""What `/live` can see of an SDK run's queue (`lib/agent_sdk`, `_sdk_queue`).

A run regin launched holds its follow-up prompts in memory and writes no
transcript `queue-operation` entries, so the transcript path `/live` normally
derives "currently queued" from sees nothing for it. The card was therefore
left showing only the client's optimistic echo, which expires on a timer — a
steer queued behind a turn longer than that TTL disappeared from the UI while
it was still, in fact, waiting to run.

These tests pin the queue as *server* truth: present while waiting, gone the
moment its turn starts, and dropped entirely when the operator stops the run.
"""

from __future__ import annotations

import asyncio

import pytest

from lib.agent_sdk import registry, runner as runner_mod
from lib.settings import settings

from tests.agent_sdk.test_runner_session import (  # noqa: F401
    FakeClient, captured, fake_client,
)

_TRACE = "sdk-queue-1"


@pytest.fixture(autouse=True)
def _clean():
    yield
    registry.unregister_run(_TRACE)


def _run(coro):
    return asyncio.run(coro)


async def _started(trace_id):
    run = runner_mod.AgentRunner(trace_id)
    await run.start()
    return run


def test_queued_prompts_are_visible_oldest_first(captured, fake_client):
    async def scenario():
        run = await _started(_TRACE)
        assert run.pending_prompts() == []
        run.enqueue("one")
        assert run.pending_prompts() == ["one"]
        run.enqueue("two")
        run.enqueue("three")
        return run.pending_prompts()

    assert _run(scenario()) == ["one", "two", "three"]


def test_a_prompt_leaves_the_queue_when_its_turn_starts(captured, fake_client):
    """The chip must not linger past the point the prompt span lands — that
    gap is what made a consumed steer look like it was still waiting."""
    seen: list[list[str]] = []

    async def scenario():
        run = await _started(_TRACE)
        original = fake_client.query

        async def query(text):
            seen.append(run.pending_prompts())
            await original(text)

        fake_client.query = query
        run.enqueue("first")
        run.enqueue("second")
        run.close()
        await run.pump()
        await run.stop()
        return run.pending_prompts()

    leftover = _run(scenario())
    # Inside turn 1 only "second" is still waiting; by turn 2 nothing is.
    assert seen == [["second"], []]
    assert leftover == []


def test_stopping_drops_what_was_still_queued(captured, fake_client):
    """`request_stop` means "stop", not "run three more prompts first" — so
    the queue the card shows has to empty with it."""
    async def scenario():
        run = await _started(_TRACE)
        run.enqueue("a")
        run.enqueue("b")
        await run.request_stop()
        pending = run.pending_prompts()
        await run.stop()
        return pending

    assert _run(scenario()) == []


def test_registry_reads_the_queue_through_the_child_session_alias(
        captured, fake_client):
    """An operator can be looking at `/live` on the id the CLI reports rather
    than the run's own — both must reach the one queue."""
    async def scenario():
        run = await _started(_TRACE)
        run.enqueue("waiting")
        registry.register_alias("child-session", _TRACE)
        return (registry.queued_prompts(_TRACE),
                registry.queued_prompts("child-session"),
                registry.queued_prompts("never-ours"))

    own, aliased, stranger = _run(scenario())
    assert own == ["waiting"]
    assert aliased == ["waiting"]
    assert stranger == []


def _own(monkeypatch, pending):
    """Make `_queued_prompts` see an owned run holding `pending`."""
    from lib import agent_sdk
    monkeypatch.setattr(agent_sdk, "is_sdk_owned", lambda tid: True)
    monkeypatch.setattr(agent_sdk, "queued_prompts", lambda tid: list(pending))


def test_the_owned_run_queue_is_served_verbatim_in_fifo_order(monkeypatch):
    from web.blueprints.trace import sessions

    _own(monkeypatch, ["oldest", "middle", "newest"])

    served = sessions._queued_prompts("sdk-run")

    assert [q["content"] for q in served] == ["oldest", "middle", "newest"]
    assert {q["source"] for q in served} == {"sdk"}


def test_identical_queued_prompts_are_not_collapsed(monkeypatch):
    """Three "continue"s are three queue entries and drop one at a time —
    deduping them by body would show one chip that only clears on the last."""
    from web.blueprints.trace import sessions

    _own(monkeypatch, ["continue", "continue", "continue"])

    assert len(sessions._queued_prompts("sdk-run")) == 3


def test_an_owned_run_ignores_the_bridge_audit_rows_of_its_own_steers(
        monkeypatch):
    """`bridge.py` records every SDK steer as a delivered `bridge_messages`
    row. That window is retired from the TRANSCRIPT, which an owned run does
    not have — so consulting it would keep a consumed steer's chip up for the
    whole 90s window and serve it newest-first, inverting the real queue.
    """
    from web.blueprints.trace import sessions

    monkeypatch.setattr(settings.agent_bridge, "enabled", True)
    monkeypatch.setattr(
        sessions, "_recent_bridge_steers",
        lambda tid: [{"content": "already consumed", "delivered_at": "x"}])

    # Its turn has started, so the runner no longer holds it.
    _own(monkeypatch, [])
    assert sessions._queued_prompts("sdk-run") == []

    # And while it IS still queued, the queue's own order is what is served.
    _own(monkeypatch, ["first", "second"])
    served = sessions._queued_prompts("sdk-run")
    assert [q["content"] for q in served] == ["first", "second"]


def test_a_session_regin_does_not_own_still_takes_the_transcript_path(
        monkeypatch):
    from lib import agent_sdk
    from web.blueprints.trace import sessions

    monkeypatch.setattr(agent_sdk, "is_sdk_owned", lambda tid: False)
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    monkeypatch.setattr(sessions, "_recent_bridge_steers", lambda tid: [])

    assert sessions._queued_prompts("terminal-session") == []


def test_the_launch_prompt_is_not_advertised_as_a_queued_steer(
        captured, fake_client):
    """The prompt a run was launched with is its first turn, not a message
    waiting behind one — a chip for it would greet every freshly-opened run."""
    async def scenario():
        run = runner_mod.AgentRunner(_TRACE)
        run.enqueue("the job", waiting=False)
        await run.start()
        # The window the card can poll in: reachable, pump not yet running.
        before = run.pending_prompts()
        run.enqueue("a real steer")
        during = run.pending_prompts()
        run.close()
        await run.pump()
        await run.stop()
        return before, during

    before, during = _run(scenario())
    assert before == []
    assert during == ["a real steer"]
