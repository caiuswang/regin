"""Feature #137 — a `one_shot` task run ends on the agent's report, not on its
first turn's result: SDK runs close their queue on the report, tmux runs are
ended by the pane's own `Stop` hook after it."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import task_run_end
from lib.agent_sdk import registry, supervisor
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTaskRun
from lib.tasks import run_ending, runs
from tests.tasks.test_tmux_launch import (  # noqa: F401 — fixtures
    _armed, _run, no_sdk, tmux)


@pytest.fixture
def ended(monkeypatch):
    calls = []
    monkeypatch.setattr(supervisor, "end_after_turn",
                        lambda trace_id: calls.append(trace_id) or (True, ""))
    return calls


def _finish(client, run_id):
    return client.post(f"/api/tasks/runs/{run_id}/finish",
                       json={"status": "done", "reason": "built it"})


def _stop(trace_id):
    task_run_end.handle_stop(HookPayload(event="Stop", session_id=trace_id))


def _kills(state):
    return [c for c in state.calls if c[0] == "kill-session"]


# ── SDK ──────────────────────────────────────────────────────

def test_a_one_shot_sdk_task_launches_steerable(flask_client, no_sdk):
    task_id = _armed(flask_client, one_shot=True)
    _run(flask_client, task_id)

    assert "one_shot" not in no_sdk[0]


def test_the_report_ends_a_one_shot_sdk_run(flask_client, no_sdk, ended):
    run = _run(flask_client, _armed(flask_client, one_shot=True))

    assert _finish(flask_client, run["id"]).status_code == 200

    assert ended == [run["trace_id"]]
    assert "end_requested" not in str(
        flask_client.get(f"/api/tasks/{run['task_id']}/runs").get_json())


def test_the_report_leaves_a_steerable_sdk_run_open(flask_client, no_sdk,
                                                    ended):
    run = _run(flask_client, _armed(flask_client))

    _finish(flask_client, run["id"])

    assert ended == []


def test_a_refused_report_ends_nothing(flask_client, no_sdk, ended,
                                       monkeypatch):
    run = _run(flask_client, _armed(flask_client, one_shot=True))
    monkeypatch.setattr(runs, "_open_fix_defects", lambda task_id: [7])

    assert _finish(flask_client, run["id"]).status_code == 409

    assert ended == []
    with SessionLocal() as session:
        assert "end_requested" not in session.get(
            AgentTaskRun, run["id"]).params_json


def test_end_after_turn_closes_the_queue_without_interrupting(monkeypatch):
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    closed = threading.Event()
    runner = SimpleNamespace(loop=loop, is_stopping=False,
                             close=lambda reason: closed.set(),
                             interrupt=lambda: pytest.fail("interrupted"))
    monkeypatch.setattr(registry, "_live_runner", lambda t: (runner, ""))
    try:
        assert supervisor.end_after_turn("t-1")[0] is True
        assert closed.wait(2)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        loop.close()


def test_end_after_turn_refuses_a_run_nobody_owns():
    assert supervisor.end_after_turn("no-such-run") == (
        False, "no live agent session")


# ── tmux ─────────────────────────────────────────────────────

def test_stop_after_the_report_ends_a_one_shot_tmux_run(flask_client, tmux):
    task_id = _armed(flask_client, launch="tmux", one_shot=True)
    run = _run(flask_client, task_id)
    name = f"task-{task_id}-a{run['attempt']}"

    _stop(run["trace_id"])
    assert name in tmux.live

    _finish(flask_client, run["id"])
    assert name in tmux.live, "the report's own turn is still running"

    _stop(run["trace_id"])
    assert name not in tmux.live
    assert len(_kills(tmux)) == 1


def test_stop_leaves_a_steerable_tmux_run_alone(flask_client, tmux):
    task_id = _armed(flask_client, launch="tmux")
    run = _run(flask_client, task_id)

    _finish(flask_client, run["id"])
    _stop(run["trace_id"])

    assert f"task-{task_id}-a{run['attempt']}" in tmux.live
    assert _kills(tmux) == []


def test_stop_for_a_session_that_is_no_task_run_is_a_no_op(flask_client, tmux):
    assert run_ending.end_reported_tmux("not-a-task-run") is False
    assert run_ending.end_reported_tmux("") is False
    assert _kills(tmux) == []


def test_the_hook_runs_after_every_other_stop_handler():
    from hook_manager.registry import REGISTRY

    stop = [h for h in REGISTRY if "Stop" in h.events]
    last = max(stop, key=lambda h: h.priority)
    assert last.name == "task_run_end_on_stop"
