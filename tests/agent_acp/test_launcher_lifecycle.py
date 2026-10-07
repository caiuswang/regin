"""Feature #140 D6 — an ACP task run settles like an SDK arm, not per turn.

A turn ending `end_turn` is not the work being done: only the agent's
`task finish` report settles the attempt `done`. A turn that stops for any
other reason, or the channel closing without a report, settles it `failed`.
The rest pins the launch surface the channel used to drop or refuse: the
armed model, the task's images, and an absolute `session/new` cwd.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from lib.agent_acp import registry as acp_registry
from lib.agent_acp.launcher import LaunchRefused, launch_run

FAKE = str(Path(__file__).parent / "fake_acp_server.py")
WAIT = 20
PNG = {"media_type": "image/png", "data_b64": "aGVsbG8="}


def _until(predicate, timeout=WAIT, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {what}")


def _requests(record: Path, method: str) -> list[dict]:
    if not record.exists():
        return []
    rows = [json.loads(line) for line in record.read_text().splitlines()]
    return [r for r in rows if r.get("method") == method]


@pytest.fixture(autouse=True)
def _close_live_runs():
    yield
    for handle in list(acp_registry._handles.values()):
        handle.close()


@pytest.fixture
def record(tmp_db, tmp_path) -> Path:
    """Where the fake logs what it was sent. Depends on `tmp_db` because
    every launch writes its `agent_runs` row."""
    return tmp_path / "acp-requests.jsonl"


def _launch(record, prompt="go", env_extra=None, **kwargs):
    env = {"FAKE_ACP_RECORD": str(record)}
    env.update(env_extra or {})
    return launch_run(prompt, cmd=[sys.executable, FAKE], env=env, **kwargs)


# ── task-level: the attempt row is what D6 is about ──────────────────

@pytest.fixture
def channel(monkeypatch, record):
    """Route `runs.start_run`'s channel arm to the real launcher over the
    fake server; `env_extra` is the fake's knobs for the next launch."""
    from lib.tasks import runs

    state = {"env_extra": {}}

    def _runner(prompt, **kwargs):
        env = {**(kwargs.pop("env") or {}),
               "FAKE_ACP_RECORD": str(record), **state["env_extra"]}
        return launch_run(prompt, cmd=[sys.executable, FAKE], env=env,
                          **kwargs)

    monkeypatch.setattr(runs, "_channel_launcher", lambda _pid: _runner)
    monkeypatch.setattr(runs, "_launch_images", lambda _task: [])
    return state


@pytest.fixture
def kimi_task(tmp_db, tmp_path):
    """An armed task with a kimi attempt open, → `(task_id, run_id)`."""
    from lib.orm import SessionLocal
    from lib.orm.models.tasks import AgentTask, TASK_STATUS_ARMED
    from lib.tasks import runs

    def _make(**params):
        with SessionLocal() as session:
            task = AgentTask(title="kimi task", status=TASK_STATUS_ARMED,
                             source="test")
            session.add(task)
            session.commit()
            session.refresh(task)
            task_id = task.id
        row = runs.arm(task_id, params={
            "provider": "kimi", "cwd": str(tmp_path), **params},
            verify_cmd=None, autorun=0)
        return task_id, row["id"]
    return _make


def _attempt(run_id: int):
    from lib.orm import SessionLocal
    from lib.orm.models.tasks import AgentTaskRun

    with SessionLocal() as session:
        return session.get(AgentTaskRun, run_id)


def _settled(run_id: int):
    return _until(lambda: (_attempt(run_id).ended_at is not None
                           and _attempt(run_id)), what="attempt settle")


def _live_handle(trace_id: str):
    return _until(lambda: acp_registry.get(trace_id), what="live handle")


def test_acp_non_end_turn_stop_reason_fails_attempt(kimi_task, channel):
    from lib.tasks import runs

    channel["env_extra"] = {"FAKE_ACP_STOP_REASON": "max_tokens"}
    task_id, run_id = kimi_task()
    view = runs.start_run(task_id)

    row = _settled(run_id)
    assert row.status == "failed"
    assert "max_tokens" in (row.exit_reason or "")
    # Settling the attempt does not hang up: the session stays steerable.
    assert acp_registry.is_owned(view["trace_id"])


def test_acp_launch_turn_end_does_not_settle_without_finish(kimi_task,
                                                            channel):
    from lib.tasks import runs

    task_id, run_id = kimi_task()
    view = runs.start_run(task_id)
    handle = _live_handle(view["trace_id"])
    _until(lambda: handle.turns, what="launch turn")
    assert handle.turns[0].status == "exited"
    time.sleep(0.3)

    assert _attempt(run_id).ended_at is None
    assert runs.latest_attempt(task_id)["status"] == "running"
    assert not handle.done()


def test_acp_finish_report_settles_attempt(kimi_task, channel):
    from lib.tasks import runs

    task_id, run_id = kimi_task()
    view = runs.start_run(task_id)
    handle = _live_handle(view["trace_id"])
    _until(lambda: handle.turns, what="launch turn")

    result = runs.finish_run(run_id, reason="did the work")
    assert result["settled"] is True
    assert _attempt(run_id).status == "done"

    # The channel closing afterwards must not rewrite the report.
    handle.close()
    assert handle.wait(timeout=WAIT).status == "failed"
    row = _attempt(run_id)
    assert row.status == "done"
    assert row.exit_reason == "did the work"


def test_acp_one_shot_closes_channel_after_turn(record):
    handle = _launch(record, one_shot=True)

    outcome = handle.wait(timeout=WAIT)
    assert outcome.status == "failed"
    assert outcome.detail.startswith("channel closed")
    assert [t.status for t in handle.turns] == ["exited"]
    assert not acp_registry.is_owned(handle.trace_id)
    assert handle._client.pid is None


def test_acp_one_shot_unreported_attempt_fails(kimi_task, channel):
    from lib.tasks import runs

    task_id, run_id = kimi_task(one_shot=True)
    view = runs.start_run(task_id)

    row = _settled(run_id)
    assert row.status == "failed"
    assert "channel closed" in (row.exit_reason or "")
    assert not acp_registry.is_owned(view["trace_id"])


def test_acp_end_after_turn_closes_steerable_run(record):
    from lib.agent_sdk import supervisor

    handle = _launch(record, env_extra={"FAKE_ACP_STALL": "0.4"})
    _until(lambda: handle.turn_in_flight, what="turn in flight")
    handle.enqueue("dropped by the end")

    ended, _detail = supervisor.end_after_turn(handle.trace_id)

    assert ended is True
    assert handle.wait(timeout=WAIT).status == "failed"
    assert len(handle.turns) == 1
    assert not acp_registry.is_owned(handle.trace_id)


def test_acp_arm_model_applied_via_set_model(tmp_db, record, tmp_path):
    from lib.agent_sdk import store as sdk_store

    handle = _launch(record, model="kimi-k2", cwd=str(tmp_path))

    (req,) = _requests(record, "session/set_model")
    assert req["params"] == {"sessionId": handle.trace_id,
                             "modelId": "kimi-k2"}
    row = sdk_store.find_run(handle.trace_id)
    assert row["model"] == "kimi-k2"
    assert row["cwd"] == str(tmp_path)


def test_acp_rejected_model_refuses_launch(record):
    with pytest.raises(LaunchRefused, match="kimi-k2"):
        _launch(record, model="kimi-k2",
                env_extra={"FAKE_ACP_REJECT_MODEL": "1"})
    assert _requests(record, "session/prompt") == []


def test_acp_images_sent_as_content_blocks(record):
    handle = _launch(record, images=[PNG])
    _until(lambda: handle.turns, what="launch turn")

    (req,) = _requests(record, "session/prompt")
    assert req["params"]["prompt"] == [
        {"type": "text", "text": "go"},
        {"type": "image", "mimeType": "image/png", "data": "aGVsbG8="}]


def test_acp_images_refused_without_image_capability(kimi_task, channel,
                                                     monkeypatch, record):
    from lib.tasks import runs

    channel["env_extra"] = {"FAKE_ACP_NO_IMAGE": "1"}
    monkeypatch.setattr(runs, "_launch_images", lambda _task: [PNG])
    task_id, run_id = kimi_task()

    with pytest.raises(runs.RunRefused, match="promptCapabilities.image"):
        runs.start_run(task_id)
    assert _attempt(run_id).status == "failed"
    assert _requests(record, "session/new") == []


def test_acp_session_new_cwd_is_absolute(tmp_db, record, monkeypatch,
                                         tmp_path):
    from lib.agent_sdk import store as sdk_store
    from lib.settings import settings

    handle = _launch(record)

    (req,) = _requests(record, "session/new")
    cwd = req["params"]["cwd"]
    assert os.path.isabs(cwd)
    assert cwd == os.path.abspath(settings.project_root)
    assert sdk_store.find_run(handle.trace_id)["cwd"] == cwd

    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path)
    _launch(record, cwd="sub")
    assert (_requests(record, "session/new")[-1]["params"]["cwd"]
            == os.path.abspath("sub"))


def test_acp_stop_mid_turn_settles_with_stop_reason(record):
    handle = _launch(record, env_extra={"FAKE_ACP_STALL": "0.5"})
    _until(lambda: handle.turn_in_flight, what="turn in flight")

    stopped, _detail = handle.stop()

    assert stopped is True
    assert handle.wait(timeout=WAIT).detail == "channel closed: stopped"


def test_acp_busy_launch_turn_closes_channel(record):
    handle = _launch(record, env_extra={"FAKE_ACP_BUSY": "1"})

    assert handle.wait(timeout=WAIT).detail == "turn.agent_busy"
    _until(lambda: not acp_registry.is_owned(handle.trace_id),
           what="channel close")


# ── defect #266: the trace row ends with the channel ────────────────

def _trace_row(trace_id: str, status: str = "active") -> None:
    from sqlalchemy import text

    from lib.orm import SessionLocal

    with SessionLocal() as session:
        session.exec(text(
            "INSERT INTO sessions (trace_id, started_at, last_seen, agent_type,"
            " status) VALUES (:t, '2026-10-07 10:00:00',"
            " '2026-10-07 10:05:00', 'kimi', :s)"
        ).bindparams(t=trace_id, s=status))
        session.commit()


def _trace_state(trace_id: str):
    from sqlalchemy import text

    from lib.orm import SessionLocal

    with SessionLocal() as session:
        return tuple(session.exec(text(
            "SELECT status, ended_at, ended_reason FROM sessions"
            " WHERE trace_id = :t").bindparams(t=trace_id)).first())


def test_acp_close_ends_the_trace_session(record):
    handle = _launch(record)
    _trace_row(handle.trace_id)
    _until(lambda: handle.turns, what="the launch turn")

    handle.end_after_turn()
    handle.wait(timeout=WAIT)

    _until(lambda: _trace_state(handle.trace_id)[0] == "ended",
           what="the trace row to end")
    assert _trace_state(handle.trace_id) == (
        "ended", "2026-10-07 10:05:00", "ended after the turn")


def test_acp_close_spares_a_session_owned_again(record, monkeypatch):
    from lib.agent_sdk import registry as sdk_registry

    handle = _launch(record)
    _trace_row(handle.trace_id)
    monkeypatch.setattr(sdk_registry, "is_sdk_owned", lambda _tid: True)

    handle.close()

    assert _trace_state(handle.trace_id) == ("active", None, None)
