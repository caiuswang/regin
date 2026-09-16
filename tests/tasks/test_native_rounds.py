"""`rounds` on a native feature — a recorded event, never a derivation (CAI-266).

`rounds = 1 + completions_walked_back`, and the column is incremented by
exactly one edge: writing `running` over a claimed `done` on a parent. The
three other exits from `done` are filing corrections — nothing was built, so
nothing was walked back.

The one live re-arm in the database is readable only from timestamps, and regin
records no declared act behind it. So there is no backfill, and `1` means
*built once, never walked back* — never "we could not look", because a local
read cannot fail that way.
"""

from __future__ import annotations

import pytest

from lib.defects import rounds
from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, RUN_STATUS_RUNNING, TASK_STATUS_ARCHIVED, TASK_STATUS_DONE,
    TASK_STATUS_FAILED, TASK_STATUS_IDEA,
)
from lib.tasks import grain, store
from lib.tasks.integrations.regin import ReginProvider


def _row(task_id: int) -> AgentTask:
    with SessionLocal() as session:
        return session.get(AgentTask, task_id)


def _rounds_via_provider(task_id: int) -> int:
    from lib.tasks.integrations.base import ExternalRef

    ref = ExternalRef(provider="regin", key=f"regin:{task_id}",
                      url=f"/tasks/{task_id}")
    return ReginProvider().fetch_issue(ref).rounds


# ── the one incrementing edge ────────────────────────────────

def test_writing_running_over_done_increments_completions_walked_back(
        native_task, task_run_row):
    parent = native_task(status=TASK_STATUS_DONE, claimed=True)
    child = native_task(parent_id=parent)
    task_run_row(child, trace_id="t-1", status=RUN_STATUS_RUNNING,
                 ended_at=None)

    grain.recompute_parent(child, event=grain.EVENT_RUN_STARTED)
    assert _row(parent).completions_walked_back == 1
    assert _rounds_via_provider(parent) == 2


# ── the three filing corrections ─────────────────────────────

def test_done_to_idea_counts_no_round(native_task):
    """The un-claim. Nothing was built between the claim and withdrawing it."""
    task_id = native_task(status=TASK_STATUS_DONE)
    store.update_task(task_id, {"status": TASK_STATUS_IDEA})
    assert _row(task_id).completions_walked_back == 0
    assert _rounds_via_provider(task_id) == 1


def test_done_to_archived_counts_no_round(native_task):
    """Filing work away is not building it, so nothing was walked back."""
    task_id = native_task(status=TASK_STATUS_DONE)
    store.update_task(task_id, {"status": TASK_STATUS_ARCHIVED})
    assert _row(task_id).completions_walked_back == 0
    assert _rounds_via_provider(task_id) == 1


def test_done_to_failed_counts_no_round(native_task):
    """The third filing correction, reached by the run lifecycle rather than
    the edit surface — nothing was built, so nothing was walked back."""
    task_id = native_task(status=TASK_STATUS_DONE)
    store.set_status(task_id, TASK_STATUS_FAILED)
    assert _row(task_id).completions_walked_back == 0
    assert _rounds_via_provider(task_id) == 1


# ── the derivation ───────────────────────────────────────────

def test_rounds_from_task_is_one_plus_the_column(native_task):
    task_id = native_task(completions_walked_back=3)
    assert rounds.from_task(_row(task_id)) == 4


def test_rounds_is_never_stored(native_task):
    """The count and the reported number cannot drift if only one exists."""
    import sqlite3

    from lib.orm.engine import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    try:
        names = {r[1] for r in conn.execute("PRAGMA table_info(agent_tasks)")}
    finally:
        conn.close()
    assert "rounds" not in names
    assert "completions_walked_back" in names


def test_rounds_from_task_takes_the_row_and_issues_no_query(
        native_task, counted_queries):
    row = _row(native_task(completions_walked_back=2))
    with counted_queries() as count:
        assert rounds.from_task(row) == 3
    assert count.value == 0


def test_a_non_parent_task_reads_rounds_one(native_task):
    assert _rounds_via_provider(native_task()) == 1


def test_a_never_run_parent_reads_rounds_one(native_task):
    parent = native_task()
    grain.set_parent(native_task(), parent)
    assert _rounds_via_provider(parent) == 1


def test_the_counter_is_never_decremented_or_reset(native_task, task_run_row):
    """The precedent is `escapes`, which is all-time. Stated cost: a hard
    DELETE of rows cannot bring this number back into agreement with them."""
    parent = native_task(status=TASK_STATUS_DONE, claimed=True)
    child = native_task(parent_id=parent)
    task_run_row(child, trace_id="t-1", status=RUN_STATUS_RUNNING,
                 ended_at=None)
    grain.recompute_parent(child, event=grain.EVENT_RUN_STARTED)
    assert _row(parent).completions_walked_back == 1

    # Every later move leaves it standing: a status is a projection of current
    # state, a walked-back completion is an event.
    grain.set_parent(child, None)
    assert _row(parent).completions_walked_back == 1
    store.ship_task(parent)
    assert _row(parent).completions_walked_back == 1
    store.update_task(parent, {"status": TASK_STATUS_IDEA})
    assert _row(parent).completions_walked_back == 1


def test_a_child_event_on_an_archived_parent_mints_no_round(
        native_task, task_run_row):
    parent = native_task(status=TASK_STATUS_ARCHIVED)
    child = native_task(parent_id=parent)
    task_run_row(child, trace_id="t-1", status=RUN_STATUS_RUNNING,
                 ended_at=None)
    grain.recompute_parent(child, event=grain.EVENT_RUN_STARTED)
    assert _row(parent).completions_walked_back == 0


def test_a_missing_native_task_says_no_such_task_not_the_tracker_did_not_answer(
        capsys):
    from typer.testing import CliRunner
    from cli.commands.feature import feature_app

    result = CliRunner().invoke(feature_app, ["stage", "regin:9999"])
    combined = result.output + (str(result.exception) if result.exception
                                else "")
    assert "no task 9999" in combined
    assert "did not answer" not in combined


def test_feature_stage_still_exits_two_for_an_unclaimable_ref():
    from typer.testing import CliRunner
    from cli.commands.feature import feature_app

    result = CliRunner().invoke(feature_app, ["stage", "not-a-ref-at-all!!"])
    assert result.exit_code == 2


def test_one_reads_built_once_not_we_could_not_look(native_task):
    """A local read cannot fail the way that fear describes."""
    task_id = native_task()
    assert _rounds_via_provider(task_id) == 1
    assert _row(task_id).completions_walked_back == 0
