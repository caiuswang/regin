"""Shared waits for the SDK-tier tests.

A park is not synchronous with the call that triggers it: the runner posts the
request span and fires its notification first, and both are thread hops. Tests
therefore have to wait for one, and every one of them had rolled its own fixed
iteration count — budgets that held running a file alone and not under
`-n auto`, where the whole suite competes for the same threads. One such test
flaked exactly that way, and its timeout fell through into an assertion about
`tool_use_id`s, so a lost race reported itself as a routing bug.

These wait on a deadline instead of a loop count, and fail saying what they
were still waiting for.
"""

from __future__ import annotations

import asyncio

from lib.agent_sdk import registry

# Generous on purpose: this bounds a hang, it does not pace a passing test,
# which returns as soon as the park lands.
PARK_TIMEOUT_SEC = 15.0


async def await_parks(trace_id: str, count: int = 1,
                      timeout: float = PARK_TIMEOUT_SEC):
    """Wait until `count` calls are parked on `trace_id`, and return them."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        parked = registry.pending_asks(trace_id)
        if len(parked) >= count:
            return parked
        if loop.time() >= deadline:
            raise AssertionError(
                f"only {len(parked)} of {count} calls parked on {trace_id} "
                f"within {timeout}s")
        await asyncio.sleep(0.005)
