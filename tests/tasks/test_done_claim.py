"""The done-claim — an operator shipping a native feature (CAI-273).

A child run settling `done` means *this session finished*, never *this feature
shipped*. Deriving the ship claim from it would flip a feature to `shipped`
when its first build session ended and turn every later defect into an escape,
making the pipeline's "a defect before the done-claim costs no round"
unreachable. So the claim is an explicit operator transition, and these tests
name both halves: what the claim is, and what it is refused on.
"""

from __future__ import annotations

import pytest

from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, RUN_STATUS_DONE, RUN_STATUS_RUNNING, TASK_STATUS_ARCHIVED,
    TASK_STATUS_DONE, TASK_STATUS_FAILED, TASK_STATUS_IDEA, TASK_STATUS_RUNNING,
)
from lib.tasks import grain, store


def _row(task_id: int) -> AgentTask:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


def _status(task_id: int) -> str:
    return _row(task_id).status


def _walked_back(task_id: int) -> int:
    return _row(task_id).completions_walked_back


# ── what the claim is not ────────────────────────────────────

def test_a_child_run_settling_done_never_ships_the_feature(
        native_task, task_run_row):
    parent = native_task()
    child = native_task(parent_id=parent)
    task_run_row(child, trace_id="t-1", status=RUN_STATUS_DONE)
    grain.recompute_parent(child)
    assert _status(parent) != TASK_STATUS_DONE


def test_all_settled_with_one_done_reads_running(native_task, task_run_row):
    """Built, awaiting the claim — not shipped."""
    parent = native_task()
    first = native_task(parent_id=parent)
    second = native_task(parent_id=parent)
    task_run_row(first, trace_id="t-1", status="failed")
    task_run_row(second, trace_id="t-2", status=RUN_STATUS_DONE)
    grain.recompute_parent(second)
    assert _status(parent) == TASK_STATUS_RUNNING


# ── what the claim is ────────────────────────────────────────

@pytest.mark.parametrize("start", [TASK_STATUS_IDEA, TASK_STATUS_RUNNING,
                                   TASK_STATUS_FAILED])
def test_an_explicit_transition_to_done_is_the_ship_claim(native_task, start):
    task_id = native_task(status=start)
    assert store.ship_task(task_id)["status"] == TASK_STATUS_DONE


def test_a_childless_task_is_claimable(native_task):
    """How a spec-only feature ships: declared, specced, never run."""
    task_id = native_task(status=TASK_STATUS_IDEA)
    assert store.ship_task(task_id)["status"] == TASK_STATUS_DONE


def test_the_claim_is_refused_on_a_task_with_a_parent_id(native_task):
    parent = native_task()
    child = native_task(parent_id=parent)
    with pytest.raises(store.TransitionRejected) as caught:
        store.ship_task(child)
    assert "run" in str(caught.value)


def test_the_claim_is_accepted_on_a_task_that_holds_runs(
        native_task, task_run_row):
    """Its own runs are the sessions that built it (regin:76)."""
    task_id = native_task()
    task_run_row(task_id, trace_id="t-1")
    assert store.ship_task(task_id)["status"] == "done"


def test_the_claim_is_refused_from_archived(native_task):
    task_id = native_task(status=TASK_STATUS_ARCHIVED)
    with pytest.raises(store.TransitionRejected):
        store.ship_task(task_id)


def test_done_to_idea_un_claims_and_costs_no_round(native_task):
    task_id = native_task(status=TASK_STATUS_DONE)
    assert store.update_task(
        task_id, {"status": TASK_STATUS_IDEA})["status"] == TASK_STATUS_IDEA
    assert _walked_back(task_id) == 0


# ── the claim under child events ─────────────────────────────

def test_a_child_run_starting_over_a_claimed_done_walks_the_claim_back(
        native_task, task_run_row):
    parent = native_task(status=TASK_STATUS_DONE, claimed=True)
    child = native_task(parent_id=parent)
    task_run_row(child, trace_id="t-1", status=RUN_STATUS_RUNNING,
                 ended_at=None)

    grain.recompute_parent(child, event=grain.EVENT_RUN_STARTED)
    assert _status(parent) == TASK_STATUS_RUNNING
    assert _walked_back(parent) == 1


def test_child_attach_and_detach_no_op_over_a_claimed_done(
        native_task, task_run_row):
    parent = native_task(status=TASK_STATUS_DONE, claimed=True)
    child = native_task()
    task_run_row(child, trace_id="t-1", status=RUN_STATUS_DONE)

    grain.set_parent(child, parent)
    assert _status(parent) == TASK_STATUS_DONE and _walked_back(parent) == 0

    grain.set_parent(child, None)
    assert _status(parent) == TASK_STATUS_DONE and _walked_back(parent) == 0


def test_a_run_settling_over_a_claimed_done_no_ops(native_task, task_run_row):
    parent = native_task(status=TASK_STATUS_DONE, claimed=True)
    child = native_task(parent_id=parent)
    task_run_row(child, trace_id="t-1", status=RUN_STATUS_DONE)

    grain.recompute_parent(child, event=grain.EVENT_RUN_SETTLED)
    assert _status(parent) == TASK_STATUS_DONE
    assert _walked_back(parent) == 0


# ── reach ────────────────────────────────────────────────────

def test_regin_task_ship_works_with_the_server_stopped(native_task, monkeypatch):
    """A done-claim releases no dependents, so it writes the DB directly."""
    import urllib.request

    def _explode(*args, **kwargs):
        raise AssertionError("regin task ship went over HTTP")

    monkeypatch.setattr(urllib.request, "urlopen", _explode)

    from typer.testing import CliRunner
    from cli.commands.tasks import tasks_app

    task_id = native_task(status=TASK_STATUS_IDEA)
    result = CliRunner().invoke(tasks_app, ["ship", str(task_id)])
    assert result.exit_code == 0, result.output
    assert _status(task_id) == TASK_STATUS_DONE


def test_there_is_no_feature_ship_verb():
    """A `feature ship` that worked only for `regin:*` would be a caller
    branching on provider."""
    from cli.commands.feature import feature_app

    names = {command.name for command in feature_app.registered_commands}
    assert "ship" not in names


def test_a_claim_writes_the_issue_cache_from_the_store(native_task):
    from lib.tasks import issue_cache

    task_id = native_task(title="shipped thing")
    store.ship_task(task_id)
    snapshot, _ = issue_cache.lookup("regin", f"regin:{task_id}")
    assert snapshot is not None
    assert snapshot["state_category"] == "completed"


def test_a_failed_cache_write_never_fails_the_status_write(
        native_task, monkeypatch):
    from lib.tasks import issue_cache

    def _explode(*args, **kwargs):
        raise RuntimeError("the cache is on fire")

    monkeypatch.setattr(issue_cache, "remember", _explode)
    task_id = native_task(status=TASK_STATUS_IDEA)
    assert store.ship_task(task_id)["status"] == TASK_STATUS_DONE


def test_a_defect_filed_before_the_claim_is_not_an_escape(
        native_task, defect_row):
    """The whole reason the claim cannot be derived from a run settling."""
    from lib.defects import escapes as escapes_module

    task_id = native_task(status=TASK_STATUS_RUNNING)
    key = f"regin:{task_id}"
    defect_row(key)
    assert escapes_module.counts_for(key).escapes == 0


def test_a_claim_racing_a_run_start_loses_no_fact(native_task, task_run_row):
    """Both orderings are already defined; the transaction boundary decides."""
    parent = native_task(status=TASK_STATUS_IDEA)
    child = native_task(parent_id=parent)

    # run-start, then claim: the claim stands and the later settle no-ops.
    task_run_row(child, trace_id="t-1", status=RUN_STATUS_RUNNING,
                 ended_at=None)
    grain.recompute_parent(child, event=grain.EVENT_RUN_STARTED)
    assert _status(parent) == TASK_STATUS_RUNNING and _walked_back(parent) == 0
