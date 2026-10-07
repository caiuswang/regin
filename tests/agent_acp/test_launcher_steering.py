"""Channel-launched (ACP) runs are owned, steerable and queued like SDK runs.

Feature #141: a kimi task run was invisible to the ownership layer — the
session page offered no composer ("not steerable"), a send had nowhere to
go, and there was no queue to show. These tests pin the ownership contract:
registered while live, steerable, FIFO prompt queue drained turn-by-turn,
gone at close.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from lib.agent_acp import registry
from lib.agent_acp.launcher import launch_run

FAKE = str(Path(__file__).parent / "fake_acp_server.py")
WAIT = 20


def _launch(tmp_path, prompt="first", env_extra=None, **kwargs):
    env = {"FAKE_ACP_BUSY": "0"}
    env.update(env_extra or {})
    return launch_run(prompt, cwd=str(tmp_path),
                      cmd=[sys.executable, FAKE], env=env, **kwargs)


def _wait_turns(handle, count, timeout=WAIT):
    """Poll `handle.turns` until `count` outcomes landed (or time out)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(handle.turns) >= count:
            return handle.turns
        time.sleep(0.05)
    pytest.fail(f"only {len(handle.turns)} turns landed, wanted {count}")


def test_run_registers_while_live_and_unregisters_at_close(tmp_path):
    handle = _launch(tmp_path)
    assert registry.is_owned(handle.trace_id)
    assert _wait_turns(handle, 1)[0].status == "exited"
    handle.close()
    assert not registry.is_owned(handle.trace_id)


def test_queued_prompt_runs_as_followup_turn(tmp_path):
    # FAKE_ACP_SERIAL rejects a prompt that arrives while a turn is open —
    # exactly what the real server answers. The queue must hold the steer
    # until the in-flight turn ends instead of failing the run with
    # turn.agent_busy.
    handle = _launch(tmp_path, env_extra={"FAKE_ACP_SERIAL": "1",
                                          "FAKE_ACP_STALL": "0.4"})
    try:
        assert handle.enqueue("second") is True
        turns = _wait_turns(handle, 2)
        assert [t.status for t in turns] == ["exited", "exited"]
    finally:
        handle.close()


def test_queued_prompts_lists_pending_and_clears(tmp_path):
    handle = _launch(tmp_path, env_extra={"FAKE_ACP_STALL": "0.4"})
    try:
        assert handle.enqueue("behind you") is True
        assert [q["text"] for q in handle.queued_prompts()] == ["behind you"]
        _wait_turns(handle, 2)
        assert handle.queued_prompts() == []
    finally:
        handle.close()


def test_enqueue_refused_after_close(tmp_path):
    handle = _launch(tmp_path)
    _wait_turns(handle, 1)
    handle.close()
    assert handle.enqueue("too late") is False
    assert not registry.is_owned(handle.trace_id)


def test_cancel_queued_drops_before_its_turn(tmp_path):
    handle = _launch(tmp_path, env_extra={"FAKE_ACP_STALL": "0.4"})
    try:
        handle.enqueue("never mind")
        (entry,) = handle.queued_prompts()
        assert handle.cancel_queued(entry["id"]) is True
        assert handle.queued_prompts() == []
        _wait_turns(handle, 1)
        time.sleep(0.2)
        assert len(handle.turns) == 1        # the cancelled turn never ran
    finally:
        handle.close()


def test_first_turn_outcome_survives_a_busy_followup(tmp_path):
    # An end_turn launch turn leaves the run unsettled (D6): only a failed
    # turn or the close settles the handle.
    handle = _launch(tmp_path)
    try:
        assert _wait_turns(handle, 1)[0].status == "exited"
        assert not handle.done()
    finally:
        handle.close()


# --- the agent_sdk seams: ownership, submit, queue, stop -------------------

def test_agent_sdk_seams_dispatch_to_acp_run(tmp_path):
    from lib import agent_sdk
    handle = _launch(tmp_path, env_extra={"FAKE_ACP_STALL": "0.4"})
    try:
        assert agent_sdk.is_sdk_owned(handle.trace_id)
        delivered, detail = agent_sdk.submit_prompt(handle.trace_id, "steer!")
        assert delivered is True
        queued = agent_sdk.queued_prompts(handle.trace_id)
        assert [q["text"] for q in queued] == ["steer!"]
        stopped, _ = agent_sdk.stop_run(handle.trace_id)
        assert stopped is True
    finally:
        handle.close()
    assert not agent_sdk.is_sdk_owned(handle.trace_id)
    delivered, detail = agent_sdk.submit_prompt(handle.trace_id, "gone")
    assert delivered is False
    assert "no longer running" in detail
