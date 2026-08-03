"""Cancelling the turn without ending the session (`runner.interrupt`).

Stop was the only control `/live` had, and it kills the session. Cancel is the
other half: end the turn, keep the child, keep the queue. The load-bearing case
is a CLI that honours an interrupt *silently* — the pump waits on the turn's
result, so without a latch of its own it would sit there forever and leave a
session alive on paper that will never run another turn.
"""

from __future__ import annotations

import asyncio

import pytest

from lib.agent_sdk import registry, runner as runner_mod
from lib.settings import settings

from tests.agent_sdk.test_runner_session import (  # noqa: F401
    FakeClient, ResultMessage, captured, fake_client,
)

_TRACE = "sdk-interrupt-1"


@pytest.fixture(autouse=True)
def _clean():
    yield
    registry.unregister_run(_TRACE)


def _run(coro):
    return asyncio.run(coro)


async def _settle(predicate, timeout=3.0):
    """Yield to the loop until `predicate` holds, or give up."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def _silence_first_turn(fake_client):
    """Make the first prompt produce no result — the CLI that never answers."""
    original = fake_client.turn_frames

    def frames():
        return () if len(fake_client.prompts) == 1 else original()

    fake_client.turn_frames = frames


def _sink(run):
    """The placeholder holding the floor for an abandoned turn, if any."""
    return next((t for t in run._open if t.sink), None)


async def _pumping(trace_id=_TRACE):
    run = runner_mod.AgentRunner(trace_id)
    await run.start()
    return run, asyncio.ensure_future(run.pump())


def test_a_silently_honoured_interrupt_still_releases_the_pump(
        captured, fake_client, monkeypatch):
    """The wedge this exists to prevent: no result comes back, so only the
    grace period can end the turn — and the session must survive it."""
    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 1)
    _silence_first_turn(fake_client)

    async def scenario():
        run, pump = await _pumping()
        run.enqueue("the one that hangs")
        run.enqueue("the one after it")
        assert await _settle(lambda: fake_client.prompts == ["the one that hangs"])

        await run.interrupt()

        ran_on = await _settle(
            lambda: fake_client.prompts[-1:] == ["the one after it"], timeout=5)
        stopping = run.is_stopping
        run.close()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return ran_on, stopping

    ran_on, stopping = _run(scenario())
    assert ran_on, "the pump never got past the interrupted turn"
    # Cancel is not Stop: the session was still taking work.
    assert stopping is False


def test_an_interrupt_leaves_the_queue_alone(captured, fake_client,
                                             monkeypatch):
    """`request_stop` drops what is queued — someone who pressed Stop did not
    mean "run three more prompts first". Cancel means the opposite: this turn
    was wrong, the ones behind it were not."""
    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 1)
    _silence_first_turn(fake_client)

    async def scenario():
        run, pump = await _pumping()
        run.enqueue("hangs")
        run.enqueue("still wanted")
        run.enqueue("also still wanted")
        assert await _settle(lambda: fake_client.prompts == ["hangs"])

        await run.interrupt()
        await _settle(lambda: len(fake_client.prompts) == 3, timeout=5)
        run.close()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return fake_client.prompts

    assert _run(scenario()) == ["hangs", "still wanted", "also still wanted"]


def test_the_interrupt_reaches_the_cli(captured, fake_client):
    """A latch alone would abandon the turn locally while the child kept
    working — the control message is what actually stops it."""
    async def scenario():
        run, pump = await _pumping()
        run.enqueue("go")
        assert await _settle(lambda: fake_client.prompts == ["go"])

        await run.interrupt()
        run.close()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return fake_client.interrupts

    assert _run(scenario()) >= 1


def test_an_interrupt_asked_for_while_idle_does_not_kill_the_next_turn(
        captured, fake_client, monkeypatch):
    """A stale latch is the obvious way to get this wrong: the operator taps
    cancel just as the turn ends, and the NEXT prompt is abandoned before it
    has said anything."""
    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 1)

    async def scenario():
        run, pump = await _pumping()
        run.enqueue("first")
        assert await _settle(lambda: fake_client.prompts == ["first"])
        # Nothing is running by now — the fake answers immediately.
        await _settle(lambda: not run.turn_in_flight)
        await run.interrupt()

        run.enqueue("second")
        landed = await _settle(
            lambda: any(s["name"] == "assistant_response"
                        and s["attributes"].get("text") == "answer 2"
                        for s in captured["spans"]), timeout=5)
        run.close()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return landed

    assert _run(scenario()), "the turn after an idle interrupt was abandoned"


def test_the_registry_routes_an_interrupt_through_the_child_alias(
        captured, fake_client):
    """An operator can be on `/live` under the id the CLI reports."""
    async def scenario():
        run, pump = await _pumping()
        registry.register_alias("child-session", _TRACE)
        run.enqueue("go")
        assert await _settle(lambda: fake_client.prompts == ["go"])

        delivered = registry.interrupt_run("child-session")
        await _settle(lambda: fake_client.interrupts >= 1)
        run.close()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return delivered, fake_client.interrupts

    delivered, interrupts = _run(scenario())
    assert delivered == (True, "interrupt sent")
    assert interrupts >= 1


def test_interrupting_a_session_this_process_does_not_hold_is_refused():
    assert registry.interrupt_run("never-ours") == (
        False, "no live agent session")


def test_a_late_result_does_not_close_the_turn_after_it(captured, fake_client,
                                                        monkeypatch):
    """A CLI that answers the interrupt *after* the grace period has a result
    still owed. `_result_turn` hands one to whatever is open, so without a sink
    holding the floor that result would end the NEXT turn and leave the pump a
    result out of phase for good — reachable only now that an interrupt returns
    to the pump instead of ending it.
    """
    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 1)
    _silence_first_turn(fake_client)

    async def scenario():
        run, pump = await _pumping()
        run.enqueue("the one that hangs")
        assert await _settle(lambda: fake_client.prompts == ["the one that hangs"])

        await run.interrupt()
        # The abandoned turn is gone, but its result has not arrived; the floor
        # is held so a straggler cannot land on someone else's turn. Settle on
        # the SINK, not on `_open` — the real turn is still open for the whole
        # grace period.
        assert await _settle(lambda: _sink(run) is not None, timeout=5)
        held = _sink(run)

        # …and the card must not read "running" while that placeholder stands.
        idle_meanwhile = run.turn_in_flight

        # The straggler the CLI finally sends for the turn that was abandoned.
        fake_client.push(ResultMessage())
        assert await _settle(lambda: held not in run._open, timeout=5)

        # The next turn still runs, and runs to completion on its OWN result.
        run.enqueue("the one after it")
        ran_on = await _settle(
            lambda: fake_client.prompts[-1:] == ["the one after it"], timeout=5)
        run.close()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return idle_meanwhile, ran_on

    idle_meanwhile, ran_on = _run(scenario())
    assert idle_meanwhile is False, "a sink must not read as the agent working"
    assert ran_on


def test_a_result_that_never_arrives_strands_nothing(captured, fake_client,
                                                     monkeypatch):
    """The sink carries no `done`, so a CLI that stays silent forever costs the
    session nothing — the next prompt retires the placeholder on its way in."""
    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 1)
    _silence_first_turn(fake_client)

    async def scenario():
        run, pump = await _pumping()
        run.enqueue("hangs")
        assert await _settle(lambda: fake_client.prompts == ["hangs"])
        await run.interrupt()
        assert await _settle(lambda: _sink(run) is not None, timeout=5)

        # No result is ever pushed for the abandoned turn.
        run.enqueue("still runs")
        ran = await _settle(
            lambda: fake_client.prompts[-1:] == ["still runs"], timeout=5)
        run.close()
        await asyncio.wait_for(pump, timeout=5)
        await run.stop()
        return ran

    assert _run(scenario())


def test_a_stop_leaves_no_sink_behind(captured, fake_client, monkeypatch):
    """Only an interrupt goes on to another turn. A stop ends the pump, so
    holding the floor after one would just leave a turn open through teardown.
    """
    monkeypatch.setattr(settings.agent_sdk, "stop_grace_sec", 1)
    _silence_first_turn(fake_client)

    async def scenario():
        run, pump = await _pumping()
        run.enqueue("hangs")
        assert await _settle(lambda: fake_client.prompts == ["hangs"])

        await run.request_stop()
        await asyncio.wait_for(pump, timeout=10)
        open_turns = list(run._open)
        await run.stop()
        return open_turns

    assert _run(scenario()) == []
