"""The two grains — a parent task is a feature, its children are runs (CAI-263).

The grains stay disjoint because three symmetric guards refuse at the act, not
at read: a task with children cannot be armed, a task with runs cannot take a
child, and a dep edge between a parent and its own child is refused. Depth is
exactly two, which is what makes acyclicity free — a cycle needs a child that
is also a parent, and the rule forbids one.

The parent's status is *written* by child events and recomputed from the
children in the same transaction, never bumped. A parent reading `archived` is
terminal against every trigger; a parent reading a claimed `done` moves on
exactly one edge, which the done-claim tests own.
"""

from __future__ import annotations

import pytest

from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, RUN_STATUS_DONE, RUN_STATUS_FAILED, RUN_STATUS_RUNNING,
    TASK_STATUS_ARCHIVED, TASK_STATUS_ARMED, TASK_STATUS_FAILED,
    TASK_STATUS_IDEA, TASK_STATUS_RUNNING,
)
from lib.tasks import deps, grain, query, store


def _status(task_id: int) -> str:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id).status


# ── depth and the guards ─────────────────────────────────────

def test_a_task_may_take_a_parent_only_if_that_parent_is_top_level(
        native_task):
    parent = native_task()
    child = native_task()
    grain.set_parent(child, parent)

    grandchild = native_task()
    with pytest.raises(grain.GrainRejected) as caught:
        grain.set_parent(grandchild, child)
    assert "two" in str(caught.value) or "top-level" in str(caught.value)


def test_arming_a_task_that_has_children_is_refused(native_task):
    parent = native_task()
    grain.set_parent(native_task(), parent)
    with pytest.raises(store.TransitionRejected):
        store.arm_task(parent, params={}, verify_cmd=None, autorun=0)


def test_attaching_a_child_to_a_task_that_has_runs_is_refused(
        native_task, task_run_row):
    holder = native_task()
    task_run_row(holder, trace_id="t-1")
    with pytest.raises(grain.GrainRejected):
        grain.set_parent(native_task(), holder)


def test_a_dep_edge_between_a_parent_and_its_own_child_is_refused(native_task):
    """"waits for" and "is a run of" are two axes; one pair cannot carry both."""
    parent = native_task()
    child = native_task()
    grain.set_parent(child, parent)

    with pytest.raises(deps.DepRejected):
        deps.set_deps(child, [parent])
    with pytest.raises(deps.DepRejected):
        deps.set_deps(parent, [child])


def test_parent_id_is_settable_at_capture_and_at_edit(native_task):
    parent = native_task()
    created, _ = store.capture_task(title="a run", source="test",
                                    parent_id=parent)
    assert created["parent_id"] == parent

    moved = native_task()
    assert store.update_task(moved, {"parent_id": parent})["parent_id"] == parent


def test_assignee_is_settable_at_edit_and_never_at_capture(native_task):
    from lib.tasks import capture

    with pytest.raises(capture.CaptureRejected) as caught:
        capture.parse_capture({"title": "t", "source": "test", "assignee": 1})
    assert caught.value.field == "assignee"

    assert "assignee" in capture.parse_update({"assignee": 7})


def test_an_unresolvable_assignee_serializes_as_null(native_task):
    task_id = native_task(assignee=99999)
    assert store.get_task(task_id)["assignee_name"] is None
    assert store.get_task(task_id)["assignee"] == 99999


def test_root_only_and_parent_id_filter_the_feed(native_task):
    parent = native_task(title="parent")
    child = native_task(title="child")
    grain.set_parent(child, parent)
    loner = native_task(title="loner")

    roots = _feed({"root_only": "1"})
    assert {parent, loner} <= roots and child not in roots
    assert _feed({"parent_id": str(parent)}) == {child}


def _feed(args: dict) -> set:
    from sqlmodel import select

    filters = query.parse_filters(args)
    with SessionLocal() as session:
        stmt = query.apply_filters(select(AgentTask), filters)
        return {row.id for row in session.exec(stmt).all()}


def test_the_default_task_feed_is_unchanged(native_task):
    """Adding the grain must not hide a run from the backlog by default."""
    parent = native_task(title="parent")
    child = native_task(title="child")
    grain.set_parent(child, parent)

    assert _feed({}) == {parent, child}
    filters = query.parse_filters({})
    assert filters["root_only"] is False and filters["parent_id"] is None


# ── the parent's status ──────────────────────────────────────

def test_a_child_run_starting_writes_the_parent_running(
        native_task, task_run_row):
    parent = native_task()
    child = native_task(parent_id=parent)
    task_run_row(child, trace_id="t-1", status=RUN_STATUS_RUNNING,
                 ended_at=None)
    grain.recompute_parent(child)
    assert _status(parent) == TASK_STATUS_RUNNING


def test_the_parent_status_is_recomputed_from_its_children_never_bumped(
        native_task, task_run_row):
    """Every child failed → the parent reads `failed`, even from `running`."""
    parent = native_task(status=TASK_STATUS_RUNNING)
    first = native_task(parent_id=parent)
    second = native_task(parent_id=parent)
    task_run_row(first, trace_id="t-1", status=RUN_STATUS_FAILED)
    task_run_row(second, trace_id="t-2", status=RUN_STATUS_FAILED)

    grain.recompute_parent(first)
    assert _status(parent) == TASK_STATUS_FAILED


def test_child_attach_and_detach_recompute_the_parent(
        native_task, task_run_row):
    parent = native_task()
    child = native_task()
    task_run_row(child, trace_id="t-1", status=RUN_STATUS_DONE)

    # Attaching an already-run child is the common adoption path: the parent
    # must not be stranded at `idea` with finished work hanging off it.
    grain.set_parent(child, parent)
    assert _status(parent) == TASK_STATUS_RUNNING

    grain.set_parent(child, None)
    assert _status(parent) == TASK_STATUS_IDEA


def test_archived_is_terminal_against_every_child_event(
        native_task, task_run_row):
    parent = native_task(status=TASK_STATUS_ARCHIVED)
    child = native_task(parent_id=parent)
    task_run_row(child, trace_id="t-1", status=RUN_STATUS_RUNNING,
                 ended_at=None)

    grain.recompute_parent(child)
    assert _status(parent) == TASK_STATUS_ARCHIVED


def test_a_child_that_has_never_run_is_not_settled(native_task, task_run_row):
    """A parent with unstarted work never reads as built-and-waiting."""
    parent = native_task()
    ran = native_task(parent_id=parent)
    never_ran = native_task(parent_id=parent)
    task_run_row(ran, trace_id="t-1", status=RUN_STATUS_DONE)

    grain.recompute_parent(ran)
    # One done, one that has never run: not all settled, so not `failed`, and
    # `running` is what "built, awaiting the claim" reads as.
    assert _status(parent) == TASK_STATUS_RUNNING

    with SessionLocal() as session:
        session.delete(session.get(AgentTask, never_ran))
        session.commit()


def test_a_parent_with_no_children_reads_idea(native_task):
    parent = native_task(status=TASK_STATUS_RUNNING)
    child = native_task(parent_id=parent)
    grain.set_parent(child, None)
    assert _status(parent) == TASK_STATUS_IDEA


def test_a_parent_is_never_armed_and_holds_no_runs(native_task, task_run_row):
    parent = native_task()
    grain.set_parent(native_task(), parent)
    with pytest.raises(store.TransitionRejected):
        store.arm_task(parent, params={}, verify_cmd=None, autorun=0)
    assert store.get_task(parent)["status"] != TASK_STATUS_ARMED
