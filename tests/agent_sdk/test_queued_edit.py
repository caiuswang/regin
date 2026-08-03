"""Editing and dropping a prompt an SDK run has queued (`lib/agent_sdk`).

A run regin launched holds its follow-ups in its own memory, so unlike the
terminal tier's queue — which lives inside Claude Code and has no write path —
this one can be corrected before it runs. These tests pin the two things that
make that safe: an entry is named by a stable id rather than a position, and
`taken` is a hard cutoff, so a prompt whose turn has started is refused instead
of silently rewritten after the CLI already has it.
"""

from __future__ import annotations

import asyncio

import pytest

from lib.agent_sdk import registry, runner as runner_mod

from tests.agent_sdk.test_runner_session import (  # noqa: F401
    FakeClient, captured, fake_client,
)

_TRACE = "sdk-edit-1"


@pytest.fixture(autouse=True)
def _clean():
    yield
    registry.unregister_run(_TRACE)


def _run(coro):
    return asyncio.run(coro)


async def _started(trace_id=_TRACE):
    run = runner_mod.AgentRunner(trace_id)
    await run.start()
    return run


def _ids(run):
    return [q["id"] for q in run.pending_prompts()]


def _texts(run):
    return [q["text"] for q in run.pending_prompts()]


def test_every_queued_prompt_gets_its_own_id(captured, fake_client):
    """Identity, not position: two identical prompts are two entries, and the
    poll that renders them is always at least one turn behind the request that
    acts on one."""
    async def scenario():
        run = await _started()
        run.enqueue("continue")
        run.enqueue("continue")
        run.enqueue("continue")
        return _ids(run)

    ids = _run(scenario())
    assert len(ids) == 3
    assert len(set(ids)) == 3


def test_an_edit_changes_what_actually_runs(captured, fake_client):
    async def scenario():
        run = await _started()
        run.enqueue("frist")
        run.enqueue("second")
        target = _ids(run)[0]

        assert run.edit_pending(target, "first") == (True, "prompt updated")
        run.close()
        await run.pump()
        await run.stop()
        return fake_client.prompts

    assert _run(scenario()) == ["first", "second"]


def test_an_edit_keeps_the_prompt_in_its_slot(captured, fake_client):
    """Fixing a typo in the third queued message meant fixing that message,
    not moving it behind the two after it."""
    async def scenario():
        run = await _started()
        for text in ("a", "b", "c"):
            run.enqueue(text)
        run.edit_pending(_ids(run)[1], "B")
        return _texts(run)

    assert _run(scenario()) == ["a", "B", "c"]


def test_a_cancelled_prompt_never_runs(captured, fake_client):
    """The record is already inside the queue when it is dropped, so the pump
    skipping it on the way past is what makes the removal real."""
    async def scenario():
        run = await _started()
        run.enqueue("keep")
        run.enqueue("drop me")
        run.enqueue("keep too")
        target = _ids(run)[1]

        assert run.cancel_pending(target) == (True, "prompt removed")
        assert _texts(run) == ["keep", "keep too"]
        run.close()
        await run.pump()
        await run.stop()
        return fake_client.prompts

    assert _run(scenario()) == ["keep", "keep too"]


def test_cancelling_every_queued_prompt_still_ends_cleanly(captured,
                                                           fake_client):
    """The pump loops past cancelled records rather than treating one as the
    terminator — an empty queue must still reach `_CLOSE`."""
    async def scenario():
        run = await _started()
        run.enqueue("no")
        run.enqueue("also no")
        for pid in _ids(run):
            run.cancel_pending(pid)
        run.close()
        await asyncio.wait_for(run.pump(), timeout=5)
        await run.stop()
        return fake_client.prompts

    assert _run(scenario()) == []


def test_a_prompt_whose_turn_started_refuses_both_mutations(captured,
                                                            fake_client):
    """The CLI already has the text. An edit here would change nothing and a
    removal would promise a turn that is already running, so both refuse — and
    say so, because the operator's card is a poll out of date."""
    seen = {}

    async def scenario():
        run = await _started()
        original = fake_client.query

        async def query(text):
            # Inside the turn: the record is `taken` but the run is live.
            seen["edit"] = run.edit_pending(running_id, "too late")
            seen["cancel"] = run.cancel_pending(running_id)
            await original(text)

        run.enqueue("the one that runs")
        running_id = _ids(run)[0]
        fake_client.query = query
        run.close()
        await run.pump()
        await run.stop()
        return fake_client.prompts

    assert _run(scenario()) == ["the one that runs"]
    assert seen["edit"] == (False, "that prompt is no longer queued")
    assert seen["cancel"] == (False, "that prompt is no longer queued")


def test_an_unknown_id_is_refused_not_ignored(captured, fake_client):
    async def scenario():
        run = await _started()
        run.enqueue("real")
        return (run.edit_pending("q999", "x"), run.cancel_pending("q999"))

    edited, cancelled = _run(scenario())
    assert edited == (False, "that prompt is no longer queued")
    assert cancelled == (False, "that prompt is no longer queued")


def test_stopping_cancels_what_it_clears(captured, fake_client):
    """`request_stop` empties the mirror, but the records are already in the
    queue — without the cancel flag the pump could still run one before it
    reached the terminator."""
    async def scenario():
        run = await _started()
        run.enqueue("a")
        run.enqueue("b")
        await run.request_stop()
        await asyncio.wait_for(run.pump(), timeout=5)
        await run.stop()
        return fake_client.prompts, run.pending_prompts()

    sent, pending = _run(scenario())
    assert sent == []
    assert pending == []


def test_the_registry_routes_mutations_through_the_child_alias(captured,
                                                               fake_client):
    """An operator can be on `/live` under the id the CLI reports rather than
    the run's own — both have to reach the one queue."""
    async def scenario():
        run = await _started()
        registry.register_run(_TRACE, run)
        run.enqueue("typo")
        registry.register_alias("child-session", _TRACE)
        target = _ids(run)[0]

        edited = registry.edit_queued("child-session", target, "fixed")
        texts = _texts(run)
        removed = registry.cancel_queued("child-session", target)
        return edited, texts, removed, _texts(run)

    edited, texts, removed, left = _run(scenario())
    assert edited == (True, "prompt updated")
    assert texts == ["fixed"]
    assert removed == (True, "prompt removed")
    assert left == []


def test_mutating_a_run_this_process_does_not_hold_is_refused():
    """A read of a dead session's leftover queue is honest; editing it would
    report success for a change nothing will ever run."""
    assert registry.edit_queued("never-ours", "q1", "x") == (
        False, "no live agent session")
    assert registry.cancel_queued("never-ours", "q1") == (
        False, "no live agent session")
