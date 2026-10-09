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

import pytest

from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, AgentTaskRun, TASK_STATUS_RUNNING,
)
from lib.tasks import deps, runs, store

@pytest.fixture
def aborted_attempt(tmp_db, native_task):
    """A task whose session died mid-run → `(task_id, run_id)`.

    Driven through `handle_completion` with a non-`exited` outcome, which is
    exactly what an aborted stream produces in production.
    """

    def _make():
        task_id = native_task(title="a slice")
        runs.arm(task_id, params={}, autorun=0)
        run = runs.open_attempt(task_id)
        store.set_status(task_id, TASK_STATUS_RUNNING)
        outcome = type("Outcome", (), {
            "status": "failed",
            "detail": "agent stream failed: Failed to decode JSON"})()
        runs.handle_completion(task_id, run["id"], outcome)
        return task_id, run["id"]

    return _make


def _task(task_id) -> AgentTask:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


def _run(run_id) -> AgentTaskRun:
    with SessionLocal() as session:
        return session.get(AgentTaskRun, run_id)


def _report(run_id, **kwargs) -> dict:
    return runs.finish_run(run_id, **kwargs)


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
    store.arm_task(dependent, params={}, autorun=0)
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
    runs.arm(task_id, params={}, autorun=0)
    run = runs.open_attempt(task_id)
    store.set_status(task_id, TASK_STATUS_RUNNING)
    _report(run["id"], status="done")

    outcome = type("Outcome", (), {"status": "failed", "detail": "killed"})()
    runs.handle_completion(task_id, run["id"], outcome)

    assert _task(task_id).status == "done"
    assert _run(run["id"]).settled_by == runs.SETTLED_BY_AGENT


# ── what a reopen must refuse ────────────────────────────────

def test_a_re_armed_task_is_not_reopened(aborted_attempt):
    """A human who re-armed the task has moved on from this attempt."""
    task_id, run_id = aborted_attempt()
    store.arm_task(task_id, params={}, autorun=0)

    settled = _report(run_id, status="done")

    assert settled["settled"] is False
    assert _task(task_id).status == "armed"
    assert _run(run_id).status == "failed"


def test_a_superseded_attempt_is_not_reopened(aborted_attempt, tmp_db):
    """A second attempt exists, so the first speaks for a run nobody watches."""
    task_id, run_id = aborted_attempt()
    runs.arm(task_id, params={}, autorun=0)
    later = runs.open_attempt(task_id)
    store.set_status(task_id, "failed")

    assert _report(run_id, status="done")["settled"] is False
    assert _task(task_id).status == "failed"
    assert _run(later["id"]).ended_at is None
