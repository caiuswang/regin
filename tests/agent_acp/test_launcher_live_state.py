"""A live ACP run reports what it is doing through the registry's live-state
read, the source every status surface (`/map.live_screen`, the /live card,
the session picker) renders — not a bare `working` with nothing behind it."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from lib import agent_sdk
from lib.agent_acp import registry as acp_registry
from lib.agent_acp.launcher import launch_run

FAKE = str(Path(__file__).parent / "fake_acp_server.py")


def _until(predicate, timeout=20, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {what}")


@pytest.fixture(autouse=True)
def _close_live_runs():
    yield
    for handle in list(acp_registry._handles.values()):
        handle.close()


def test_a_run_inside_a_tool_call_reports_the_call(tmp_db, monkeypatch):
    monkeypatch.setenv("FAKE_ACP_TOOL", "mcp__linear__authenticate")
    monkeypatch.setenv("FAKE_ACP_STALL", "3")
    handle = launch_run("go", cmd=[sys.executable, FAKE])

    owned = _until(lambda: (lambda o: o if o.state and o.state.tool else None)(
        agent_sdk.run_live_state(handle.trace_id)), what="tool in live state")
    assert owned.sdk
    assert owned.state.busy and owned.state.verb == "Working"
    assert owned.state.tool == "mcp__linear__authenticate"
    assert owned.state.ctx_pct == 25
    assert agent_sdk.run_live_states()[handle.trace_id].tool == \
        "mcp__linear__authenticate"
    assert acp_registry.open_tool_use_ids([handle.trace_id]) == {"fake-1"}

    _until(lambda: not handle.turn_in_flight, what="turn end")
    idle = agent_sdk.run_live_state(handle.trace_id).state
    assert (idle.busy, idle.verb, idle.tool) == (False, None, None)
