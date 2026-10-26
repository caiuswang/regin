"""A prompt sent while a run is still starting must be held, not refused.

`is_sdk_owned` counts a reservation, so every reader — the trace view's
follow-up bundle, `/live`'s composer gate — reports a reserved id steerable
from the moment a launch is scheduled. `_live_runner` reads `_runs` alone, so
until `register_run` lands (measured 2.45-2.60s on a resume) every send to
that composer came back "no live agent session". Only a resume reaches the
window: a fresh launch carries its prompt in the launch call.
"""

from __future__ import annotations

import asyncio

import pytest

from lib.agent_bridge import store as bridge_store
from lib.agent_sdk import registry

_TRACE = "sdk-parked-1"
_CHILD = "child-parked-1"


@pytest.fixture(autouse=True)
def _clean():
    yield
    registry.unregister_run(_TRACE)


class _FakeRunner:
    """Just the surface the registry touches: a loop, `enqueue` and `admit`."""

    def __init__(self, loop):
        self.loop = loop
        self.taken: list[tuple] = []

    def enqueue(self, text, *, waiting=True, images=None, row_id=None):
        self.taken.append((text, tuple(images or ()), row_id, waiting))

    def admit(self, text, *, images=None, row_id=None):
        return (text, tuple(images or ()), row_id, True)

    def deliver_admitted(self, item):
        self.taken.append(item)


def _row(text):
    return bridge_store.record_bridge_message(_TRACE, text, "web:test")


def _detail(row_id):
    rows = bridge_store.list_bridge_messages(session_id=_TRACE)
    return next(r["delivery_detail"] for r in rows if r["id"] == row_id)


async def _drain(loop):
    """Let the `call_soon_threadsafe` callbacks `register_run` scheduled run."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def test_a_prompt_sent_during_the_reservation_window_is_accepted():
    """The refusal this whole change exists to remove."""
    token = object()
    assert registry.reserve_run(_TRACE, token)
    try:
        queued, detail = registry.submit_prompt(_TRACE, "the first prompt")
        assert queued is True
        assert "starting" in detail
    finally:
        registry.release_run(_TRACE, token)


def test_a_parked_prompt_reaches_the_runner_when_it_registers():
    async def scenario():
        loop = asyncio.get_running_loop()
        token = object()
        assert registry.reserve_run(_TRACE, token)
        registry.submit_prompt(_TRACE, "first", images=[{"d": 1}], row_id=7)
        registry.submit_prompt(_TRACE, "second")
        runner = _FakeRunner(loop)
        registry.register_run(_TRACE, runner)
        await _drain(loop)
        return runner.taken

    taken = asyncio.run(scenario())
    assert [t[0] for t in taken] == ["first", "second"], "send order must hold"
    assert taken[0][1] == ({"d": 1},), "images must ride the park"
    assert taken[0][2] == 7, "the bridge row id must ride the park"


def test_a_parked_prompt_is_a_waiting_prompt():
    """Once released it queues behind whatever the launch already gave the
    runner, so it is steering — and from then on editable and cancellable —
    not the run's own launch turn. For the window itself it is neither:
    `queued_prompts` reads `_runs`, so a parked prompt has no chip until the
    drain, which is the cost of accepting it at all.
    """

    async def scenario():
        loop = asyncio.get_running_loop()
        registry.reserve_run(_TRACE, object())
        registry.submit_prompt(_TRACE, "parked")
        runner = _FakeRunner(loop)
        registry.register_run(_TRACE, runner)
        await _drain(loop)
        return runner.taken

    assert asyncio.run(scenario())[0][3] is True


def test_a_prompt_parked_on_a_launch_that_dies_is_reported(tmp_db):
    """`release_run` is the launch failing inside its own startup window. The
    composer cleared the draft on the ack, so the row is the last copy."""
    row_id = _row("the prompt whose launch died")
    bridge_store.mark_delivered(row_id, True, "prompt queued")
    token = object()
    assert registry.reserve_run(_TRACE, token)
    registry.submit_prompt(_TRACE, "the prompt whose launch died",
                           row_id=row_id)
    registry.release_run(_TRACE, token)

    assert "launch_released" in _detail(row_id)


def test_a_child_session_id_parks_onto_its_run():
    """The id the operator's card is open on is the child's — a resume aliases
    it at claim time, which is the only reason the window is reachable."""
    token = object()
    assert registry.reserve_run(_TRACE, token)
    registry.register_alias(_CHILD, _TRACE)
    try:
        queued, _ = registry.submit_prompt(_CHILD, "sent at the child id")
        assert queued is True
    finally:
        registry.release_run(_TRACE, token)


def test_an_unknown_id_is_still_refused():
    """Parking must not turn "this session does not exist" into an ack."""
    queued, detail = registry.submit_prompt("sdk-never-existed", "hello")
    assert queued is False
    assert detail == "no live agent session"


def test_a_live_run_does_not_park():
    """Once registered, the ordinary path owns the send — a stale reservation
    must never divert a prompt away from a runner that can take it."""

    async def scenario():
        loop = asyncio.get_running_loop()
        runner = _FakeRunner(loop)
        registry.register_run(_TRACE, runner)
        queued, detail = registry.submit_prompt(_TRACE, "live one")
        await _drain(loop)
        return queued, detail, runner.taken

    queued, detail, taken = asyncio.run(scenario())
    assert queued is True
    assert detail == "prompt queued"
    assert [t[0] for t in taken] == ["live one"]
