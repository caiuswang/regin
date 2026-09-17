"""An aborted session, resumed by hand, can still report its work finished.

The SDK callback settling a run says only *this session ended*. For a session
aborted mid-work and later resumed manually that inference is wrong, and
`ended_at` used to make it permanent: the resumed agent's `regin task finish`
hit the idempotence guard, printed "already settled — nothing to do", and the
task stayed `failed` over finished work.

`settled_by` is what tells the inference from the report, and
`reopen_for_agent_report` is what lets the report win — once, and only over the
attempt the task is still holding the failure of.
"""

from __future__ import annotations

import sys

import pytest

from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, AgentTaskRun, TASK_STATUS_RUNNING,
)
from lib.tasks import deps, runs, store

_EXIT = f"{sys.executable} -c 'import sys; sys.exit({{code}})'"


@pytest.fixture(autouse=True)
def _drain_verifies():
    yield
    for run_id in list(runs._verifying):
        runs.wait_for_verify(run_id, timeout=30)
    runs._verifying.clear()


@pytest.fixture
def aborted_attempt(tmp_db, native_task):
    """A task whose session died mid-run → `(task_id, run_id)`.

    Driven through `handle_completion` with a non-`exited` outcome, which is
    exactly what an aborted stream produces in production.
    """

    def _make(verify_cmd=None):
        task_id = native_task(title="a slice")
        runs.arm(task_id, params={}, verify_cmd=verify_cmd, autorun=0)
        run = runs.open_attempt(task_id)
        store.set_status(task_id, TASK_STATUS_RUNNING)
        outcome = type("Outcome", (), {
            "status": "failed",
            "detail": "agent stream failed: Failed to decode JSON"})()
        worker = runs.handle_completion(task_id, run["id"], outcome)
        if worker is not None:
            worker.join(timeout=60)
        return task_id, run["id"]

    return _make


def _task(task_id) -> AgentTask:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


def _run(run_id) -> AgentTaskRun:
    with SessionLocal() as session:
        return session.get(AgentTaskRun, run_id)


def _report(run_id, **kwargs) -> dict:
    result = runs.finish_run(run_id, **kwargs)
    runs.wait_for_verify(run_id, timeout=60)
    return result


# ── the aborted-then-resumed run ─────────────────────────────

def test_the_callback_stamps_the_session_as_the_settler(aborted_attempt):
    task_id, run_id = aborted_attempt()

    assert _task(task_id).status == "failed"
    row = _run(run_id)
    assert (row.status, row.settled_by) == ("failed", runs.SETTLED_BY_SESSION)


def test_a_resumed_agent_report_re_settles_the_task_done(aborted_attempt):
    task_id, run_id = aborted_attempt()

    settled = _report(run_id, status="done")

    assert settled["settled"] is True
    assert _task(task_id).status == "done"
    row = _run(run_id)
    assert (row.status, row.settled_by) == ("done", runs.SETTLED_BY_AGENT)
    assert row.exit_reason == "agent signalled finish"


def test_a_resumed_report_releases_the_dependents_the_failure_held(
        tmp_db, native_task, aborted_attempt, monkeypatch):
    """The whole cost of the dropped report: a chain parked behind a task that
    is `failed` over finished work never moves."""
    task_id, run_id = aborted_attempt()
    dependent = native_task(title="downstream")
    deps.set_deps(dependent, [task_id])
    store.arm_task(dependent, params={}, verify_cmd=None, autorun=0)
    monkeypatch.setattr(runs, "start_run", lambda tid: {"id": tid})

    assert _report(run_id, status="done")["fired"] == [dependent]


def test_a_second_agent_report_is_still_a_no_op(aborted_attempt):
    task_id, run_id = aborted_attempt()
    _report(run_id, status="done")

    again = _report(run_id, status="failed", reason="second thoughts")

    assert again["settled"] is False
    assert _task(task_id).status == "done"
    assert _run(run_id).exit_reason == "agent signalled finish"


def test_the_callback_cannot_clobber_an_agent_report(tmp_db, native_task):
    """The ordinary production order — report first, session ends after — is
    unchanged: only `finish_run` reopens, so the callback still loses."""
    task_id = native_task(title="a slice")
    runs.arm(task_id, params={}, verify_cmd=None, autorun=0)
    run = runs.open_attempt(task_id)
    store.set_status(task_id, TASK_STATUS_RUNNING)
    _report(run["id"], status="done")

    outcome = type("Outcome", (), {"status": "failed", "detail": "killed"})()
    worker = runs.handle_completion(task_id, run["id"], outcome)
    if worker is not None:
        worker.join(timeout=60)

    assert _task(task_id).status == "done"
    assert _run(run["id"]).settled_by == runs.SETTLED_BY_AGENT


# ── what a reopen must refuse ────────────────────────────────

def test_a_re_armed_task_is_not_reopened(aborted_attempt):
    """A human who re-armed the task has moved on from this attempt."""
    task_id, run_id = aborted_attempt()
    store.arm_task(task_id, params={}, verify_cmd=None, autorun=0)

    settled = _report(run_id, status="done")

    assert settled["settled"] is False
    assert _task(task_id).status == "armed"
    assert _run(run_id).status == "failed"


def test_a_superseded_attempt_is_not_reopened(aborted_attempt, tmp_db):
    """A second attempt exists, so the first speaks for a run nobody watches."""
    task_id, run_id = aborted_attempt()
    runs.arm(task_id, params={}, verify_cmd=None, autorun=0)
    later = runs.open_attempt(task_id)
    store.set_status(task_id, "failed")

    assert _report(run_id, status="done")["settled"] is False
    assert _task(task_id).status == "failed"
    assert _run(later["id"]).ended_at is None


# ── the gate survives the reopen ─────────────────────────────

def test_the_reopen_re_runs_the_verify_rather_than_inheriting_its_code(
        aborted_attempt, tmp_path):
    """The aborted attempt already left a `verify_exit_code`, and inheriting it
    would grade the resumed work by a suite that ran against the abandoned one.
    The command counts its own invocations and fails only the first, so a task
    that reaches `done` can only have been graded twice."""
    counter = tmp_path / "runs"
    script = (f"import pathlib,sys; p=pathlib.Path({str(counter)!r}); "
              "n=len(p.read_text()) if p.exists() else 0; "
              "p.write_text('x'*(n+1)); sys.exit(0 if n else 1)")
    task_id, run_id = aborted_attempt(
        verify_cmd=f"{sys.executable} -c {script!r}")
    assert (_task(task_id).status, _run(run_id).verify_exit_code) == ("failed", 1)

    assert _report(run_id, status="done")["verifying"] is True

    assert counter.read_text() == "xx"
    assert _task(task_id).status == "done"
    row = _run(run_id)
    assert (row.status, row.verify_exit_code) == ("done", 0)


def test_a_failing_verify_on_the_reopen_leaves_the_task_failed(
        aborted_attempt):
    task_id, run_id = aborted_attempt(verify_cmd=_EXIT.format(code=1))

    _report(run_id, status="done")

    assert _task(task_id).status == "failed"
    row = _run(run_id)
    assert (row.status, row.verify_exit_code) == ("failed", 1)
    assert row.settled_by == runs.SETTLED_BY_AGENT
