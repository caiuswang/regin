"""Feature #140 — task runs launched through a provider's channel (kimi ACP).

The kimi arm never touches the Claude launch path: `supervisor.launch_run`
must not be called, and the trace id is the ACP session id the channel hands
back (no sdk-* placeholder / alias dance — the child is known at launch).
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from types import SimpleNamespace

import pytest

from lib.orm.models.tasks import TASK_STATUS_ARMED, RUN_STATUS_RUNNING
from lib.tasks import runs


class _FakeAcpHandle:
    """The launcher's handle surface, minus the subprocess."""

    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self.callback = None

    def add_done_callback(self, callback) -> None:
        self.callback = callback

    def finish(self, status: str = "failed",
               detail: str = "channel closed: closed") -> None:
        from lib.agent_acp.launcher import RunOutcome
        self.callback(RunOutcome(self.trace_id, status, detail))


@pytest.fixture
def acp_launcher(monkeypatch):
    """A stubbed `lib.agent_acp.launcher.launch_run`, recording every call."""
    import sys

    state = SimpleNamespace(handles=[], calls=[])
    sdk_calls = []

    def _fake(prompt, **kwargs):
        state.calls.append({"prompt": prompt, **kwargs})
        handle = _FakeAcpHandle(f"session_fake{len(state.handles):04d}")
        state.handles.append(handle)
        return handle

    monkeypatch.setattr(runs, "_channel_launcher", lambda _pid: _fake)
    monkeypatch.setattr(runs.supervisor, "launch_run",
                        lambda *a, **k: sdk_calls.append((a, k)))
    state.sdk_calls = sdk_calls
    return state


def _arm_kimi(task_id: int) -> None:
    runs.open_attempt(task_id, params={"provider": "kimi",
                                       "cwd": "/tmp/kimi-repo"})


def test_task_run_acp_launch_uses_provider_channel(tmp_db, native_task,
                                                   acp_launcher):
    from lib.orm import SessionLocal
    from lib.orm.models.tasks import AgentTaskRun
    from sqlmodel import select

    task_id = native_task(title="kimi task", status=TASK_STATUS_ARMED)
    _arm_kimi(task_id)

    view = runs.start_run(task_id)

    assert view["status"] == RUN_STATUS_RUNNING
    assert view["trace_id"].startswith("session_")
    assert acp_launcher.calls, "kimi run must go through the ACP launcher"
    assert not acp_launcher.sdk_calls, "kimi run must not touch the Claude SDK"
    handle = acp_launcher.handles[0]
    assert handle.callback is not None
    # The channel only reports failures (D6); its close settles the attempt
    # through the shared completion path.
    handle.finish()
    with SessionLocal() as session:
        row = session.exec(select(AgentTaskRun)
                           .where(AgentTaskRun.task_id == task_id)
                           .order_by(AgentTaskRun.id.desc())).first()
    assert row.status == "failed"


def _wait_settled(task_id: int, timeout: float = 300) -> dict:
    deadline = time.time() + timeout
    settled = None
    while time.time() < deadline:
        settled = runs.latest_attempt(task_id)
        if settled and settled.get("status") not in (None, "armed",
                                                     "running"):
            return settled
        time.sleep(2)
    return settled or {}


def _trace_row(trace_id: str, real_db: str, timeout: float = 30):
    """Poll the REAL trace DB — tmp_db isolates the attempt row, not the
    child the launcher spawned."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with sqlite3.connect(real_db) as con:
            row = con.execute(
                "SELECT span_count FROM sessions WHERE trace_id = ?",
                (trace_id,)).fetchone()
        if row and row[0]:
            return row
        time.sleep(2)
    return None


@pytest.mark.skipif(not shutil.which("kimi"), reason="kimi CLI not installed")
def test_active_provider_kimi_task_run_end_to_end(tmp_db, native_task):
    """Real-binary smoke: one kimi task attempt through the real `kimi acp`.
    Gated behind REGIN_E2E_KIMI=1 — it spends a real API call. `one_shot`
    closes the channel when the turn ends so the child never outlives the
    test."""
    import os

    if os.environ.get("REGIN_E2E_KIMI") != "1":
        pytest.skip("set REGIN_E2E_KIMI=1 to spend a real API call")

    # pytest blackholes ingest for spawned children (REGIN_INGEST_BASE_URL →
    # a dead port); the e2e's kimi must post to the REAL server or its trace
    # never lands. Overriding the overlay back is the production shape:
    # server → launcher → kimi → :8321 ingest.
    from lib.orm import engine
    from lib.settings import _MAIN_WORKTREE, settings

    real_db = str(settings.trace_db_path
                  or _MAIN_WORKTREE / "db" / "regin.db")
    ingest = f"http://127.0.0.1:{settings.web_port}"

    # A bare title reads as open-ended work: kimi spends the one turn
    # exploring the repo for something to merge and never reports.
    task_id = native_task(
        title="kimi e2e", status=TASK_STATUS_ARMED,
        body="Do no other work: run the finish command below exactly as "
             "given, with --reason 'e2e ok'.")
    # The child's `task finish` must settle this test's attempt, not the
    # same-numbered one in the real DB; nothing listens on :9, so the
    # finish settles in its own process.
    runs.open_attempt(task_id,
                      params={"provider": "kimi", "cwd": "/tmp",
                              "one_shot": True,
                              "env": {"REGIN_E2E": "1",
                                      "REGIN_INGEST_BASE_URL": ingest,
                                      "REGIN_TRACE_DB_PATH": engine.DB_PATH,
                                      "REGIN_AGENT_MEMORY__INJECT_SERVER_URL":
                                          "http://127.0.0.1:9"}})
    view = runs.start_run(task_id)
    assert view["trace_id"].startswith("session_")
    settled = _wait_settled(task_id)
    assert settled.get("status") == "done", settled
    assert _trace_row(view["trace_id"], real_db) is not None, (
        f"{view['trace_id']} never reached the trace DB")
