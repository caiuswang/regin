"""F180 #8 — `task finish` is refused while an operator message is still owed
to the model, and a `one_shot` finish seals the run against later sends.

The SDK runner here is the real queue (`_QueueMixin`) on a real loop thread:
the seal and the admit are serialized by its lock, which is the property under
test, so a fake queue would pass against the bug.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
import urllib.error
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from lib.agent_acp import registry as acp_registry
from lib.agent_acp.launcher import AcpRunHandle
from lib.agent_bridge import delivery, pending
from lib.agent_bridge import store as bridge_store
from lib.agent_sdk import registry
from lib.agent_sdk.runner_queue import _QueueMixin
from lib.orm import SessionLocal
from lib.orm.models.tasks import AgentTask, AgentTaskRun
from lib.tasks import run_ending
from tests.tasks.test_tmux_launch import (  # noqa: F401 — fixtures
    _after, _armed, _run, no_sdk, tmux)

M = ("补充：还要支持暗色主题，并且 composer 的草稿在被拒时必须原样保留。"
     + "the preview keeps the first eighty characters and no more " * 3)
assert len(M) > 100


class _Runner(_QueueMixin):
    """An `AgentRunner`'s queue and the attributes the registry reads."""

    def __init__(self, trace_id: str, loop) -> None:
        self.trace_id = trace_id
        self.loop = loop
        self._queue_lock = threading.Lock()
        self._pending: list = []
        self._queue_seq = 0
        self._sealed = False
        self._prompts: asyncio.Queue = asyncio.Queue()
        self.is_stopping = False

    def close(self, reason: str = "stopped") -> None:
        self.is_stopping = True


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    loop.close()


@pytest.fixture
def runners(loop):
    made = []

    def _make(trace_id):
        runner = _Runner(trace_id, loop)
        registry.register_run(trace_id, runner)
        made.append(trace_id)
        return runner
    yield _make
    for trace_id in made:
        registry.unregister_run(trace_id)


@pytest.fixture
def typed(monkeypatch):
    """A tmux delivery that lands: the pane is the fake's, the row is real."""
    sent = []

    def _deliver(trace_id, text, as_command=False):
        sent.append(text)
        return SimpleNamespace(delivered=True, detail="delivered")
    monkeypatch.setattr(delivery, "deliver", _deliver)
    return sent


def _finish(client, run_id):
    return client.post(f"/api/tasks/runs/{run_id}/finish",
                       json={"status": "done", "reason": "built it"})


def _send(client, trace_id, text):
    return client.post(f"/api/sessions/{trace_id}/bridge-send",
                       json={"text": text}).get_json()


def _attempt(run_id) -> AgentTaskRun:
    with SessionLocal() as session:
        return session.get(AgentTaskRun, run_id)


def _params(run_id) -> dict:
    return json.loads(_attempt(run_id).params_json or "{}")


def _task_status(task_id) -> str:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id).status


def _cli_finish(flask_client, monkeypatch, task_id, run_id):
    """`regin task finish` with its loopback hop answered by the test app."""
    from cli.commands import tasks as cli_tasks
    from cli.regin import app

    def _opener(request, timeout):
        path = request.full_url.split("://", 1)[1].split("/", 1)[1]
        resp = flask_client.post("/" + path, data=request.data,
                                 content_type="application/json")
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, resp.status_code,
                                         "refused", {}, io.BytesIO(resp.data))
        return io.BytesIO(resp.data)
    monkeypatch.setattr(cli_tasks, "_open_loopback", _opener)
    return CliRunner().invoke(app, ["task", "finish", str(task_id),
                                    "--run", str(run_id),
                                    "--reason", "built it"])


def test_finish_refused_while_message_queued_sdk(flask_client, no_sdk,
                                                 runners, monkeypatch):
    task_id = _armed(flask_client, one_shot=True)
    downstream = _armed(flask_client, "downstream")
    _after(flask_client, downstream, task_id)
    run = _run(flask_client, task_id)
    runner = runners(run["trace_id"])
    assert _send(flask_client, run["trace_id"], M)["delivered"] is True

    result = _cli_finish(flask_client, monkeypatch, task_id, run["id"])

    assert result.exit_code == 1
    assert "尚未送达" in result.output
    assert M[:80] in result.output and M[:81] not in result.output
    assert _attempt(run["id"]).status == "running"
    assert len(no_sdk) == 1, "the downstream task must not have launched"
    assert _task_status(downstream) != "running"
    assert run_ending.END_REQUESTED not in _params(run["id"])
    assert runner.sealed is False


def test_finish_refused_while_message_queued_acp(flask_client, no_sdk):
    task_id = _armed(flask_client, one_shot=True)
    run = _run(flask_client, task_id)
    handle = AcpRunHandle(run["trace_id"], Future(),
                          SimpleNamespace(close=lambda: None),
                          run["trace_id"])
    acp_registry.register(handle)
    try:
        assert handle.enqueue(M) is True

        resp = _finish(flask_client, run["id"])

        assert resp.status_code == 409
        assert "尚未送达" in resp.get_json()["error"]
        assert _attempt(run["id"]).status == "running"
        assert handle.sealed is False
    finally:
        acp_registry.unregister(run["trace_id"])
        acp_registry._retired.discard(run["trace_id"])


def test_finish_refused_while_message_queued_tmux(flask_client, tmux, typed,
                                                  session_row):
    task_id = _armed(flask_client, launch="tmux", one_shot=True)
    run = _run(flask_client, task_id)
    session_row(run["trace_id"])
    sent = _send(flask_client, run["trace_id"], M)
    assert sent["delivered"] is True

    resp = _finish(flask_client, run["id"])

    assert resp.status_code == 409
    assert "尚未送达" in resp.get_json()["error"]
    assert _attempt(run["id"]).status == "running"
    assert run_ending.END_REQUESTED not in _params(run["id"])
    chips = flask_client.get(f"/api/sessions/{run['trace_id']}/map?shallow=1"
                             ).get_json()["queued_prompts"]
    owed = pending.pending_operator_messages(run["trace_id"])
    assert len(owed) == len(chips) == 1
    assert owed[0]["source"] == "bridge_row"


def test_idle_one_shot_finish_seals_the_run(flask_client, no_sdk, runners,
                                            tmux, typed):
    sdk_task = _armed(flask_client, "sdk", one_shot=True)
    sdk_run = _run(flask_client, sdk_task)
    runner = runners(sdk_run["trace_id"])
    tmux_task = _armed(flask_client, "tmux", launch="tmux", one_shot=True)
    tmux_run = _run(flask_client, tmux_task)
    name = f"task-{tmux_task}-a{tmux_run['attempt']}"

    for run in (sdk_run, tmux_run):
        assert _finish(flask_client, run["id"]).status_code == 200
        assert _attempt(run["id"]).status == "done"
        assert _params(run["id"])[run_ending.END_REQUESTED] is True

    assert runner.sealed is True and runner.is_stopping is True
    run_ending.end_reported_tmux(tmux_run["trace_id"])
    assert name not in tmux.live


def test_a_failed_settle_takes_the_seal_back(flask_client, no_sdk, runners,
                                             monkeypatch):
    from lib.tasks import runs

    run = _run(flask_client, _armed(flask_client, one_shot=True))
    runner = runners(run["trace_id"])

    def _boom(*args, **kwargs):
        raise RuntimeError("settle failed")
    monkeypatch.setattr(runs, "_settle_report", _boom)
    with pytest.raises(RuntimeError):
        runs.finish_run(run["id"], reason="built it")

    assert runner.sealed is False
    assert _send(flask_client, run["trace_id"], "still here")["delivered"]


def test_a_steerable_run_is_refused_but_never_sealed(flask_client, no_sdk,
                                                    runners):
    run = _run(flask_client, _armed(flask_client, one_shot=False))
    runner = runners(run["trace_id"])
    _send(flask_client, run["trace_id"], M)

    assert _finish(flask_client, run["id"]).status_code == 409
    runner.cancel_pending(runner.pending_prompts()[0]["id"])
    assert _finish(flask_client, run["id"]).status_code == 200

    assert runner.sealed is False
    assert _send(flask_client, run["trace_id"], "later")["delivered"] is True


def _client(flask_client):
    """A client of its own per thread: the fixture's holds a pushed context
    that belongs to the test's thread."""
    client = flask_client.application.test_client()
    client.environ_base.update(flask_client.environ_base)
    return client


def _race(flask_client, run, text):
    """Finish and send from two threads released by one barrier."""
    finisher, sender = (_client(flask_client), _client(flask_client))
    barrier = threading.Barrier(2)
    out = {}

    def _do_finish():
        barrier.wait()
        out["finish"] = _finish(finisher, run["id"]).status_code

    def _do_send():
        barrier.wait()
        out["send"] = _send(sender, run["trace_id"], text)
    threads = [threading.Thread(target=_do_finish),
               threading.Thread(target=_do_send)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return out["finish"], out["send"]


def _outcome(finish: int, send: dict) -> str:
    if finish == 409 and send.get("delivered") is True:
        return "finish refused, message queued"
    if finish == 200 and send.get("reason") == "run_sealed":
        return "finish accepted, send refused"
    return f"lost: finish={finish} send={send}"


RACE_ROUNDS = 200


def test_finish_and_send_race_has_one_winner(flask_client, no_sdk, runners,
                                             tmux, typed):
    outcomes: dict[str, set] = {"sdk": set(), "tmux": set()}
    for i in range(RACE_ROUNDS):
        run = _run(flask_client, _armed(flask_client, f"sdk {i}",
                                        one_shot=True))
        runners(run["trace_id"])
        outcomes["sdk"].add(_outcome(*_race(flask_client, run, f"s{i}")))

        run = _run(flask_client, _armed(flask_client, f"tmux {i}",
                                        launch="tmux", one_shot=True))
        outcomes["tmux"].add(_outcome(*_race(flask_client, run, f"t{i}")))

    allowed = {"finish refused, message queued",
               "finish accepted, send refused"}
    assert outcomes["sdk"] <= allowed, outcomes["sdk"]
    assert outcomes["tmux"] <= allowed, outcomes["tmux"]



def test_a_closing_sealed_run_still_answers_run_sealed(flask_client, no_sdk,
                                                       runners):
    """A send that read the attempt before the report wrote `end_requested`
    meets the runner already closing: it is still refused as sealed."""
    run = _run(flask_client, _armed(flask_client, one_shot=True))
    runner = runners(run["trace_id"])
    assert _finish(flask_client, run["id"]).status_code == 200
    assert runner.is_stopping is True

    assert registry.submit_prompt(run["trace_id"], "late") == (
        False, registry.SEALED_DETAIL)


@pytest.mark.parametrize("launch", ["sdk", "tmux"])
def test_a_failed_repeat_finish_keeps_the_earlier_seal(
        flask_client, no_sdk, runners, tmux, typed, monkeypatch, launch):
    from lib.tasks import runs

    run = _run(flask_client, _armed(flask_client, launch=launch,
                                    one_shot=True))
    runner = runners(run["trace_id"]) if launch == "sdk" else None
    assert _finish(flask_client, run["id"]).status_code == 200

    def _boom(*args, **kwargs):
        raise RuntimeError("settle failed")
    monkeypatch.setattr(runs, "_settle_report", _boom)
    with pytest.raises(RuntimeError):
        runs.finish_run(run["id"], reason="again")

    assert _params(run["id"])[run_ending.END_REQUESTED] is True
    assert runner is None or runner.sealed is True
    assert _send(flask_client, run["trace_id"], "late")["reason"] == (
        "run_sealed")
