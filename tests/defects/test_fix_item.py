"""How a defect points at the work item that will close it.

One foreign key at `defects.fix_task_id`, where a `(provider, external_key)`
pair used to sit. Every item regin can point at is a row in `agent_tasks` now,
so the loop closes in the schema and a reader joins it instead of re-parsing a
string. The stated cost: a defect can no longer point at a remote issue that
has no task here.

The two sides still measure different axes: `defects.status` answers *is this
bug still real*, the task's status answers *is this work still scheduled*.
regin never writes either side from the other — and the decisive reason it
cannot auto-close a defect from a finished task is that `store.close()`
requires `missing_check`, which a status transition cannot supply.
"""

from __future__ import annotations

from typer.testing import CliRunner

from cli.commands.defects import defects_app
from lib.defects import store

runner = CliRunner()


def test_the_fix_item_pointer_is_set_by_an_explicit_verb_not_by_defects_open(
        feature_row, native_task):
    """Most defects are fixed inside the session that found them, so
    auto-creation would flood the board with items that are done before anyone
    reads them."""
    feature = feature_row()
    task_id = native_task(title="fix it")
    runner.invoke(defects_app, ["open", "it broke",
                                "--feature", str(feature.id)])
    row = store.for_feature(feature.id)[0]
    assert row.fix_task_id is None

    result = runner.invoke(defects_app, ["fix-item", str(row.id),
                                         "--task", str(task_id)])

    assert result.exit_code == 0, result.output
    assert store.get(row.id).fix_task_id == task_id


def test_a_defect_has_at_most_one_fix_item(feature_row, native_task):
    """"The fix landed in two places" is a property of the fix, not evidence
    of two defects — two defects would have two symptoms and two rows."""
    from lib.orm.models.defects import Defect

    feature = feature_row()
    first, second = native_task(title="a"), native_task(title="b")
    row = store.record("it broke", feature_id=feature.id)
    store.set_fix_item(row.id, first)
    store.set_fix_item(row.id, second)

    assert store.get(row.id).fix_task_id == second
    assert "fix_task_id" in Defect.model_fields
    assert "fix_task_ids" not in Defect.model_fields


def test_one_fix_item_may_serve_many_defects(native_task):
    """The many-to-one direction is how batch cleanup actually happens — and
    the reverse lookup is the query the batch-fix case is for."""
    task_id = native_task(title="the fix")
    a = store.record("first", session_id="t-1")
    b = store.record("second", session_id="t-2")
    store.set_fix_item(a.id, task_id)
    store.set_fix_item(b.id, task_id)

    found = store.by_fix_item(task_id)

    assert sorted(r.id for r in found) == sorted([a.id, b.id])


def test_a_fix_item_that_names_no_task_is_refused(feature_row):
    """The pair it replaced validated nothing, so a typo pointed a defect at
    an item nobody could open. A foreign key is checkable, so it is checked."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)

    result = runner.invoke(defects_app, ["fix-item", str(row.id),
                                         "--task", "9999"])

    assert result.exit_code != 0
    assert store.get(row.id).fix_task_id is None


def test_closing_a_defect_never_touches_the_fix_item(feature_row, native_task):
    feature = feature_row()
    task_id = native_task(title="fix it")
    row = store.record("it broke", feature_id=feature.id)
    store.set_fix_item(row.id, task_id)

    store.close(row.id, missing_check="a regression test")

    assert store.get(row.id).fix_task_id == task_id


def test_finishing_the_fix_item_never_closes_the_defect(feature_row,
                                                        native_task):
    """An auto-close would fabricate `missing_check` — the one output of this
    ledger that changes future behaviour rather than describing past
    behaviour."""
    from lib.tasks import store as task_store

    feature = feature_row()
    task_id = native_task(title="fix it")
    row = store.record("it broke", feature_id=feature.id)
    store.set_fix_item(row.id, task_id)
    task_store.set_status(task_id, "done")

    states = store.fix_states([store.get(row.id)])

    assert states[row.id] == "done"
    assert store.get(row.id).status == "open"
    assert store.get(row.id).missing_check is None


def test_with_fix_state_is_a_local_read(feature_row, native_task, no_tracker):
    """It used to do network I/O per fix item, so it had to report `unknown`
    when no tracker answered. A task's status is in this database."""
    feature = feature_row()
    task_id = native_task(title="fix it", status="running")
    row = store.record("it broke", feature_id=feature.id)
    store.set_fix_item(row.id, task_id)

    output = runner.invoke(defects_app, ["list", "--feature", str(feature.id),
                                         "--with-fix-state"]).output

    assert "running" in output
    assert f"task {task_id}" in output


def test_with_fix_state_never_reports_in_sync(feature_row, native_task):
    """Divergence is surfaced, never reconciled — and "in sync" is a claim
    about two axes that do not measure the same thing."""
    feature = feature_row()
    task_id = native_task(title="fix it", status="done")
    row = store.record("it broke", feature_id=feature.id)
    store.set_fix_item(row.id, task_id)

    output = runner.invoke(defects_app, ["list", "--feature", str(feature.id),
                                         "--with-fix-state"]).output

    assert "in sync" not in output
    assert f"task {task_id}" in output


def test_create_refuses_on_an_unattributed_defect(no_tracker):
    """A task no feature owns is exactly the orphan late-binding attribution
    exists to avoid."""
    row = store.record("it broke", session_id="t-1")

    result = runner.invoke(defects_app, ["fix-item", str(row.id), "--create"])

    assert result.exit_code != 0
    assert "regin defects attribute" in result.output
    assert store.get(row.id).fix_task_id is None


def test_create_writes_the_symptom_verbatim_under_the_feature(feature_row):
    """Paraphrasing would make the board and the ledger describe the same bug
    differently. No label, no assignee, no priority — fields with human
    writers.

    The task lands under the feature, so its runs are sessions of that feature
    and the fix's cost rolls up where the bug is counted.
    """
    from lib.orm import SessionLocal
    from lib.orm.models.tasks import AgentTask

    feature = feature_row("the feature")
    row = store.record("the gutter folds the newest span",
                       feature_id=feature.id, session_id="t-1",
                       suspect_paths=["a.py"])

    result = runner.invoke(defects_app, ["fix-item", str(row.id), "--create"])

    assert result.exit_code == 0, result.output
    task_id = store.get(row.id).fix_task_id
    with SessionLocal() as session:
        task = session.get(AgentTask, task_id)
    assert task.title == "the gutter folds the newest span"
    assert task.feature_id == feature.id
    assert f"regin defect #{row.id}" in task.body
    assert "t-1" in task.body and "a.py" in task.body


def test_the_fix_item_pointer_is_last_writer_wins(feature_row, native_task):
    """`is_primary` is first-writer-wins because cost attribution is a claim
    about the past; a fix item points at future work and legitimately moves.
    Nothing is counted from it, so a rewrite corrupts no metric."""
    feature = feature_row()
    first, second = native_task(title="a"), native_task(title="b")
    row = store.record("it broke", feature_id=feature.id)

    store.set_fix_item(row.id, first)
    store.set_fix_item(row.id, second)
    store.clear_fix_item(row.id)

    assert store.get(row.id).fix_task_id is None


def test_fix_item_state_never_feeds_rounds_or_feature_stage(feature_row,
                                                            native_task):
    """A fix task's own churn is work-item noise; counting it would inflate a
    feature diligent enough to schedule its fixes and exonerate one that
    patched silently."""
    from lib.features import events, ledger as ledger_module

    feature = feature_row(stage=events.SHIPPED)
    task_id = native_task(title="fix it", status="done")
    row = store.record("an escape", feature_id=feature.id)
    store.set_fix_item(row.id, task_id)

    report = ledger_module.build(feature.id)

    assert report.rounds == 1
    assert report.escapes.escapes == 1
    assert "fix" not in " ".join(report.to_dict()["stage"])
