"""Cross-thread resolution of a parked question (`lib/agent_sdk/registry`).

The whole answer path hinges on a thread hop: the future is created on the
runner's event loop, and resolved from a Flask request handler on a different
thread. These tests run a real loop in a background thread rather than faking
it, because a plain `set_result` from the wrong thread is exactly the bug this
module exists to prevent and it would pass against a mock.
"""

from __future__ import annotations

import asyncio
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
