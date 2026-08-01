"""Cross-thread resolution of a parked question (`lib/agent_sdk/registry`).

The whole answer path hinges on a thread hop: the future is created on the
runner's event loop, and resolved from a Flask request handler on a different
thread. These tests run a real loop in a background thread rather than faking
it, because a plain `set_result` from the wrong thread is exactly the bug this
module exists to prevent and it would pass against a mock.
"""

from __future__ import annotations

import asyncio
import sys
import threading

import pytest

from lib.agent_sdk import registry


class _Loop:
    """A real asyncio loop running on its own thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def make_future(self):
        return asyncio.run_coroutine_threadsafe(
            self._create(), self.loop).result(timeout=5)

    async def _create(self):
        return asyncio.get_running_loop().create_future()

    def wait(self, future, timeout=5):
        return asyncio.run_coroutine_threadsafe(
            asyncio.wait_for(asyncio.shield(future), timeout),
            self.loop).result(timeout=timeout + 1)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


@pytest.fixture
def loop():
    runner = _Loop()
    yield runner
    runner.close()


@pytest.fixture(autouse=True)
def _clean():
    yield
    registry.unregister_run("t1")


def _park(loop, trace_id="t1"):
    future = loop.make_future()
    registry.register_ask(registry.PendingAsk(
        trace_id=trace_id, tool_use_id="toolu_1", tool_input={},
        future=future, loop=loop.loop))
    return future


def test_answer_from_another_thread_reaches_the_parked_future(loop):
    future = _park(loop)

    delivered, _ = registry.resolve_ask("t1", [{"option_index": 0}])

    assert delivered is True
    assert loop.wait(future) == [{"option_index": 0}]


def test_answering_twice_is_refused_not_crashed(loop):
    _park(loop)
    registry.resolve_ask("t1", [{"option_index": 0}])

    delivered, detail = registry.resolve_ask("t1", [{"option_index": 1}])

    assert delivered is False
    assert detail == "no pending question"


def test_answer_without_a_parked_question_is_a_structured_refusal():
    delivered, detail = registry.resolve_ask("nobody", [])

    assert (delivered, detail) == (False, "no pending question")


def test_answering_a_dead_session_refuses_rather_than_raising(loop):
    _park(loop)
    loop.close()

    delivered, detail = registry.resolve_ask("t1", [{"option_index": 0}])

    assert delivered is False
    assert "no longer running" in detail


def test_ownership_tracks_run_registration():
    assert registry.is_sdk_owned("t1") is False
    registry.register_run("t1", object())
    assert registry.is_sdk_owned("t1") is True
    registry.unregister_run("t1")
    assert registry.is_sdk_owned("t1") is False


def test_unregistering_a_run_drops_its_parked_question(loop):
    registry.register_run("t1", object())
    _park(loop)

    registry.unregister_run("t1")

    assert registry.get_ask("t1") is None


# ── reserving an id before its runner exists ──────────────────────────
#
# A runner registers only after `connect()` has spawned its child, so every
# check-then-launch would otherwise read "unowned" for the whole of a run's
# startup. These pin the claim that closes that window.


def test_a_reserved_id_is_owned_before_any_runner_registers():
    assert registry.reserve_run("t1") is True

    assert registry.is_sdk_owned("t1") is True


def test_a_second_reservation_of_the_same_id_is_refused():
    registry.reserve_run("t1")

    assert registry.reserve_run("t1") is False


def test_a_live_runs_id_cannot_be_reserved():
    registry.register_run("t1", object())

    assert registry.reserve_run("t1") is False


def test_releasing_hands_the_id_back():
    registry.reserve_run("t1")

    registry.release_run("t1")

    assert registry.is_sdk_owned("t1") is False
    assert registry.reserve_run("t1") is True


def test_releasing_twice_is_a_no_op():
    registry.reserve_run("t1")
    registry.release_run("t1")

    registry.release_run("t1")
    registry.release_run("never-reserved")

    assert registry.is_sdk_owned("t1") is False


def test_a_stale_release_leaves_a_later_claim_on_the_same_id_standing():
    """The id outlives the launch that held it: a run whose completion callback
    fires after a successor reserved the same trace id must not hand away the
    successor's claim — that is the original window, reopened."""
    first = object()
    registry.reserve_run("t1", first)
    registry.release_run("t1", first)
    registry.reserve_run("t1", object())

    registry.release_run("t1", first)

    assert registry.is_sdk_owned("t1") is True


def test_registering_converts_the_reservation_into_a_live_runner():
    registry.reserve_run("t1")

    registry.register_run("t1", object())

    assert registry.is_sdk_owned("t1") is True
    assert registry.active_run_count() == 1


def test_unregistering_clears_a_reservation_the_runner_never_took_up():
    registry.reserve_run("t1")

    registry.unregister_run("t1")

    assert registry.is_sdk_owned("t1") is False
    assert registry.active_run_count() == 0


def test_a_reserved_run_counts_against_capacity():
    """`max_concurrent_runs` is over-subscribable through exactly the same
    window: a slot only claimed at registration is free to a second launch."""
    assert registry.active_run_count() == 0

    registry.reserve_run("t1")

    assert registry.active_run_count() == 1
    assert registry.active_run_count(exclude="t1") == 0


def test_a_reserved_run_is_not_reachable_yet():
    """Ownership is not reachability. A reservation must not put anything in
    `_runs` for the control paths to call methods on."""
    registry.reserve_run("t1")

    assert registry.submit_prompt("t1", "hi") == (False, "no live agent session")
    assert registry.stop_run("t1") == (False, "no live agent session")
    assert registry.queued_prompts("t1") == []


def test_only_one_of_many_threads_wins_one_id():
    """Real contention, not a simulated interleaving: the test and the claim
    have to be one atomic step or two resumes both get a green light."""
    switch = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    winners: list[int] = []
    try:
        for round_no in range(40):
            trace_id = f"race-{round_no}"
            gate = threading.Barrier(8)
            won = []
            guard = threading.Lock()

            def claim(trace_id=trace_id, gate=gate, won=won):
                gate.wait(timeout=5)
                if registry.reserve_run(trace_id, object()):
                    with guard:
                        won.append(trace_id)

            threads = [threading.Thread(target=claim) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            winners.append(len(won))
            registry.unregister_run(trace_id)
    finally:
        sys.setswitchinterval(switch)

    assert winners == [1] * 40


def test_reserving_waits_on_the_registry_lock():
    """Real contention above cannot *fail* an implementation whose test and
    claim merely happen to run back to back — the gap is a few bytecodes and
    the interpreter rarely preempts inside it — so pin the mechanism too: a
    reserve that does not hold `_lock` across both halves sails through here.
    """
    claimed = threading.Event()
    with registry._lock:
        thread = threading.Thread(
            target=lambda: (registry.reserve_run("t1"), claimed.set()))
        thread.start()
        blocked = not claimed.wait(0.25)
    thread.join(timeout=5)

    assert blocked is True
    assert claimed.is_set() is True
