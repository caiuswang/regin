"""A ship claim is a recorded act, not a reading of `status` (CAI-274 #126/#127).

`agent_tasks.status` carries the *task lifecycle*: a run settling writes `done`
onto it, meaning "the work finished". CAI-274 also read `done` as
`state_category=completed`, i.e. "this feature shipped" — two axes on one
column, and the run lifecycle holding a pen for both.

The live consequence: `regin:63` was armed and run directly, its run settled
`done`, and the feature reported `shipped` with nobody having claimed it. That
is precisely what CAI-273 exists to forbid — *a run settling done never ships
the feature* — reached by a path the guard did not cover.

So the claim gets its own column. `claimed_at` is NULL until an operator
declares the feature done, and only `ship_task` and the un-claim ever write it.
`rounds` already took this shape for the same reason: a declared act is
recorded, never derived from current state.
"""

from __future__ import annotations

import pytest

from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, RUN_STATUS_DONE, TASK_STATUS_DONE, TASK_STATUS_IDEA,
    TASK_STATUS_RUNNING,
)
from lib.tasks import grain, runs, store
from lib.tasks.integrations.base import ExternalRef
from lib.tasks.integrations.regin import ReginProvider, key_for_task


def _issue(task_id: int):
    ref = ExternalRef(provider="regin", key=key_for_task(task_id),
                      url=f"/tasks/{task_id}")
    return ReginProvider().fetch_issue(ref)


def _row(task_id: int) -> AgentTask:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


# ── the escape, named ────────────────────────────────────────

def test_a_run_settling_never_ships_the_task_it_ran(native_task, task_run_row):
    """#127. The task's own run finishing is not a claim about the feature."""
    task_id = native_task(status=TASK_STATUS_RUNNING)
    run_id = task_run_row(task_id, trace_id="t-1", status="running",
                          ended_at=None)

    runs.complete_run(run_id, status=RUN_STATUS_DONE,
                      task_status=TASK_STATUS_DONE)

    # The task lifecycle moved — that much is true and useful.
    assert _row(task_id).status == TASK_STATUS_DONE
    # The feature did not. Nobody claimed it.
    assert _row(task_id).claimed_at is None
    assert _issue(task_id).state_category != "completed"


def test_only_an_operator_act_writes_the_claim(native_task):
    task_id = native_task(status=TASK_STATUS_IDEA)
    assert _row(task_id).claimed_at is None

    store.ship_task(task_id)
    assert _row(task_id).claimed_at is not None
    assert _issue(task_id).state_category == "completed"


def test_the_un_claim_clears_the_record(native_task):
    task_id = native_task(status=TASK_STATUS_IDEA)
    store.ship_task(task_id)
    store.update_task(task_id, {"status": TASK_STATUS_IDEA})

    assert _row(task_id).claimed_at is None
    assert _issue(task_id).state_category != "completed"
    # And it cost no round: nothing was built between the two.
    assert _row(task_id).completions_walked_back == 0


def test_a_task_that_holds_runs_can_still_be_reported_on(native_task,
                                                         task_run_row):
    """#126, the other half. A top-level task's own runs are the sessions
    that built it (regin:76): the key resolves while it is building and the
    claim is accepted once it has."""
    task_id = native_task(status=TASK_STATUS_RUNNING)
    task_run_row(task_id, trace_id="t-1")

    issue = _issue(task_id)
    assert issue.key == key_for_task(task_id)
    assert issue.state_category == "started"

    store.ship_task(task_id)
    assert _issue(task_id).state_category == "completed"


def test_status_alone_no_longer_decides_the_stage(native_task):
    """The regression both defects share: `done` written by anything other
    than the claim must not read as shipped."""
    task_id = native_task(status=TASK_STATUS_DONE)  # lifecycle-done, unclaimed
    assert _issue(task_id).state_category != "completed"

    store.ship_task(task_id)
    assert _issue(task_id).state_category == "completed"


def test_a_claimed_feature_survives_its_children_settling(native_task,
                                                          task_run_row):
    """The claim is not recomputed away by a child event."""
    parent = native_task()
    child = native_task(parent_id=parent)
    store.ship_task(parent)

    task_run_row(child, trace_id="t-1", status=RUN_STATUS_DONE)
    grain.recompute_parent(child, event=grain.EVENT_RUN_SETTLED)

    assert _row(parent).claimed_at is not None
    assert _issue(parent).state_category == "completed"


def test_restarting_work_walks_the_claim_back_and_clears_it(native_task,
                                                            task_run_row):
    parent = native_task()
    child = native_task(parent_id=parent)
    store.ship_task(parent)
    task_run_row(child, trace_id="t-1", status="running", ended_at=None)

    grain.recompute_parent(child, event=grain.EVENT_RUN_STARTED)

    assert _row(parent).claimed_at is None
    assert _row(parent).completions_walked_back == 1
    assert _issue(parent).rounds == 2
