"""Slice S1 — `verify_cmd` decides how an attempt settles (spec R4).

The command was a dead field: the arm sheet collected it, `runs.arm` stored it,
and nothing ever ran it (defect d158). These pin the executor, and with it
invariant 4 — a non-empty `verify_cmd` always leaves a `verify_exit_code`
behind, so "no gate" and "the gate passed" stay distinguishable.

Both settle paths are exercised: `finish_run`, the agent's own report, which is
the one a dependency chain waits on; and `handle_completion`, the SDK callback
behind it.
"""

from __future__ import annotations

import os
import sys

import pytest

from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, AgentTaskRun, TASK_STATUS_RUNNING,
)
from lib.tasks import runs, store, verify

_EXIT = f"{sys.executable} -c 'import sys; sys.exit({{code}})'"


@pytest.fixture(autouse=True)
def _drain_verifies():
    """No verify thread may outlive its test: the DB it would settle into
    belongs to the next one."""
    yield
    for run_id in list(runs._verifying):
        runs.wait_for_verify(run_id, timeout=30)
    runs._verifying.clear()


@pytest.fixture
def running_attempt(tmp_db, native_task):
    """A task mid-run → `(task_id, run_id)`, armed with `verify_cmd`."""

    def _make(verify_cmd=None, params=None):
        task_id = native_task(title="a slice")
        runs.arm(task_id, params=params or {}, verify_cmd=verify_cmd,
                 autorun=0)
        run = runs.open_attempt(task_id)
        store.set_status(task_id, TASK_STATUS_RUNNING)
        return task_id, run["id"]

    return _make


def _report(run_id, **kwargs) -> dict:
    """The agent's `task finish`, waited out.

    A claimed verify settles on a worker thread — the agent's own call must not
    straddle a test suite — so every assertion about the verdict joins it
    first.
    """
    result = runs.finish_run(run_id, **kwargs)
    runs.wait_for_verify(run_id, timeout=60)
    return result


def _task(task_id) -> AgentTask:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


def _run(run_id) -> AgentTaskRun:
    with SessionLocal() as session:
        return session.get(AgentTaskRun, run_id)


# ── R4: the exit code settles the attempt ────────────────────

def test_verify_cmd_non_zero_settles_the_run_failed(running_attempt):
    task_id, run_id = running_attempt(verify_cmd=_EXIT.format(code=1))

    assert _report(run_id, status="done")["verifying"] is True

    assert _task(task_id).status == "failed"
    row = _run(run_id)
    assert row.status == "failed"
    assert row.verify_exit_code == 1
    assert "exit 1" in row.exit_reason


def test_verify_cmd_zero_settles_the_run_done(running_attempt):
    task_id, run_id = running_attempt(verify_cmd=_EXIT.format(code=0))

    assert _report(run_id, status="done")["verifying"] is True

    assert _task(task_id).status == "done"
    row = _run(run_id)
    assert (row.status, row.verify_exit_code) == ("done", 0)


def test_absent_verify_cmd_leaves_verify_exit_code_null(running_attempt):
    task_id, run_id = running_attempt(verify_cmd=None)

    settled = _report(run_id, status="failed", reason="could not build")

    assert (settled["settled"], settled["verifying"]) == (True, False)

    assert _task(task_id).status == "failed"
    row = _run(run_id)
    assert row.verify_exit_code is None
    assert row.exit_reason == "could not build"


def test_a_verify_that_cannot_run_is_a_failure_not_a_pass(running_attempt):
    """R4's fourth row, and the one the code must never round down to 0: a
    command that did not run has proved nothing."""
    task_id, run_id = running_attempt(
        verify_cmd="regin-no-such-command-exists --please")

    _report(run_id, status="done")

    assert _task(task_id).status == "failed"
    row = _run(run_id)
    assert row.verify_exit_code == verify.NOT_RUN
    assert row.verify_exit_code != 0


def test_a_vanished_worktree_fails_rather_than_verifying_a_fresh_checkout(
        running_attempt, monkeypatch):
    """The tree the work landed in is gone, so there is nowhere to verify. The
    settle must NOT fall back to a checkout made at settle time: that would
    grade the branch's committed state and pass an attempt that produced
    nothing."""
    task_id, run_id = running_attempt(
        verify_cmd=_EXIT.format(code=0),
        params={"worktree": {"branch": "f76/gone", "base": "master"}})

    def _explode(*args, **kwargs):
        raise AssertionError("the settle path must not create a worktree")

    monkeypatch.setattr(runs.worktree, "ensure_worktree", _explode)
    monkeypatch.setattr(runs.worktree, "existing_worktree",
                        lambda *args, **kwargs: None)

    _report(run_id, status="done")

    row = _run(run_id)
    assert (_task(task_id).status, row.verify_exit_code) == ("failed",
                                                             verify.NOT_RUN)
    assert "did not run" in row.exit_reason


def test_a_second_report_does_not_spend_the_verify_command_again(
        running_attempt, tmp_path):
    """`complete_run` settles once; the verify must not run twice for it."""
    counter = tmp_path / "runs"
    task_id, run_id = running_attempt(
        verify_cmd=f"{sys.executable} -c \"open({str(counter)!r},'a').write('x')\"")

    assert _report(run_id, status="done")["verifying"] is True

    second = _report(run_id, status="done")

    assert (second["settled"], second["verifying"]) == (False, False), \
        "a run that settled minutes ago must not be reported as still verifying"
    assert counter.read_text() == "x"
    assert _run(run_id).verify_exit_code == 0


def test_the_callback_arriving_mid_verify_does_not_start_a_second_one(
        running_attempt, tmp_path, monkeypatch):
    """The production sequence: the agent reports `task finish`, its verify
    starts, and the session ends while the verify is still running — so
    `handle_completion` arrives long before `ended_at` is written. It must not
    spend the operator's suite a second time in the same tree."""
    counter = tmp_path / "runs"
    task_id, run_id = running_attempt(
        verify_cmd=f"{sys.executable} -c \"open({str(counter)!r},'a').write('x')\"")
    outcome = type("Outcome", (), {"status": "exited",
                                   "detail": "completed"})()
    seen = []

    original = runs._apply_verdict

    def _mid_verify(command, params, status, reason):
        """Fire the callback while this verify is still in flight."""
        if not seen:
            seen.append(runs.handle_completion(task_id, run_id, outcome))
        return original(command, params, status, reason)

    monkeypatch.setattr(runs, "_apply_verdict", _mid_verify)

    assert _report(run_id, status="done")["verifying"] is True

    assert seen == [None], "the callback must not have spawned a verify thread"
    assert counter.read_text() == "x"
    assert _run(run_id).verify_exit_code == 0


def test_a_claimed_verify_leaves_the_did_not_run_code_behind(running_attempt):
    """The claim is written before the subprocess starts, so a process killed
    mid-verify leaves exactly what is true of the attempt on the row."""
    task_id, run_id = running_attempt(verify_cmd=_EXIT.format(code=0))

    claim, command, _ = runs._claim_verify(run_id)

    assert claim == runs.VERIFY_MINE and command
    assert _run(run_id).verify_exit_code == verify.NOT_RUN
    assert _run(run_id).ended_at is None
    assert runs._claim_verify(run_id)[0] == runs.VERIFY_THEIRS


def test_a_gate_that_cannot_be_read_stands_down_instead_of_skipping(
        running_attempt, monkeypatch):
    """Invariant 4 is written over the row, not over what a reader managed to
    load: an attempt whose `verify_cmd` we failed to read still has one, and
    settling on the agent's word would report a pass for a verify nobody ran."""
    task_id, run_id = running_attempt(verify_cmd=_EXIT.format(code=0))

    def _unreadable(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(runs, "SessionLocal", _unreadable)

    assert runs._claim_verify(run_id)[0] == runs.VERIFY_THEIRS


def test_a_tracker_that_cannot_be_reached_still_releases_the_dependents(
        running_attempt, monkeypatch):
    """The resync is a courtesy to a remote; the firing is the chain. A raise
    reaching for the first must not swallow the second."""
    from lib.features import store as feature_store

    task_id, run_id = running_attempt(verify_cmd=_EXIT.format(code=0))
    fired = []
    monkeypatch.setattr(feature_store, "feature_of_task",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("tracker unreachable")))
    monkeypatch.setattr(runs, "fire_ready_dependents",
                        lambda tid, **_: fired.append(tid) or [])

    _report(run_id, status="done")

    assert _run(run_id).status == "done"
    assert fired == [task_id]


def test_an_attempt_with_no_worktree_verifies_in_the_project_root(
        running_attempt, monkeypatch, tmp_path):
    """Never the directory the server happened to be started in: a `pytest`
    that finds no tests exits 5 and would settle the run failed for a reason
    that has nothing to do with the work."""
    from lib.settings import settings

    monkeypatch.setattr(settings, "project_root", tmp_path)

    assert runs._verify_cwd({}) == str(tmp_path)
    assert runs._verify_cwd({"cwd": "/somewhere/else"}) == "/somewhere/else"


def test_a_launch_that_never_started_still_records_that_verify_did_not_run(
        tmp_db, native_task, monkeypatch):
    """Invariant 4 is written over any run, not only runs that started: a
    refused launch carries the armed `verify_cmd`, so a NULL code there would
    read as "this attempt had no gate"."""
    from lib.agent_sdk import supervisor

    task_id = native_task(title="a slice", status="armed")
    runs.arm(task_id, params={}, verify_cmd="pytest -q", autorun=0)
    monkeypatch.setattr(
        supervisor, "launch_run",
        lambda *a, **k: (_ for _ in ()).throw(
            supervisor.LaunchRefused("max_concurrent_runs reached")))

    with pytest.raises(runs.RunRefused):
        runs.start_run(task_id)

    row = _run(runs.latest_attempt(task_id)["id"])
    assert row.verify_cmd == "pytest -q"
    assert row.verify_exit_code == verify.NOT_RUN


# ── the SDK callback settles through the same gate ───────────

def test_the_completion_callback_also_runs_verify(running_attempt):
    task_id, run_id = running_attempt(verify_cmd=_EXIT.format(code=3))

    worker = runs.handle_completion(
        task_id, run_id,
        type("Outcome", (), {"status": "exited", "detail": "completed"})())
    worker.join(timeout=30)

    row = _run(run_id)
    assert (_task(task_id).status, row.status) == ("failed", "failed")
    assert row.verify_exit_code == 3


def test_the_callback_does_not_block_the_loop_it_shares(running_attempt):
    """The SDK loop's thread is shared by every live run, so a verify measured
    in minutes cannot be waited on there — the callback hands it off."""
    task_id, run_id = running_attempt(
        verify_cmd=f"{sys.executable} -c 'import time; time.sleep(2)'")

    worker = runs.handle_completion(
        task_id, run_id,
        type("Outcome", (), {"status": "exited", "detail": "completed"})())

    assert worker is not None and worker.is_alive()
    assert _run(run_id).ended_at is None


def test_an_attempt_without_a_verify_still_settles_inline(running_attempt):
    """The offload is for verify only: with no command the caller keeps the
    settle it has always been able to observe on return."""
    task_id, run_id = running_attempt(verify_cmd=None)

    worker = runs.handle_completion(
        task_id, run_id,
        type("Outcome", (), {"status": "exited", "detail": "completed"})())

    assert worker is None
    assert _run(run_id).status == "done"
    assert _run(run_id).verify_exit_code is None


# ── the Operating constraint: a separate, waited-on subprocess ──

def test_verify_runs_in_its_own_process_in_the_attempts_tree(tmp_path):
    """The constraint S1 was written under: verify is a child process, so a
    command that takes down its surroundings cannot take down the settle."""
    marker = tmp_path / "pid"
    verdict = verify.run_verify(
        f"{sys.executable} -c \"import os;"
        f" open('pid','w').write(str(os.getpid()))\"",
        cwd=str(tmp_path))

    assert verdict.passed and verdict.exit_code == 0
    assert marker.read_text() != str(os.getpid())


def test_a_verdict_is_read_back_before_the_settle_is_written(running_attempt,
                                                             tmp_path):
    """The settle carries the command's answer, so the write happens after the
    subprocess has been waited on — not alongside it."""
    stamp = tmp_path / "ran"
    task_id, run_id = running_attempt(
        verify_cmd=f"{sys.executable} -c \"open({str(stamp)!r},'w')\"; exit 7")

    assert _run(run_id).ended_at is None

    _report(run_id, status="done")

    assert stamp.exists()
    assert _run(run_id).verify_exit_code == 7


def test_run_verify_never_raises_on_a_command_that_cannot_start():
    verdict = verify.run_verify("true", cwd="/no/such/directory/at/all")

    assert verdict.exit_code == verify.NOT_RUN
    assert not verdict.passed
    assert "did not run" in verdict.reason
