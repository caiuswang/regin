"""Acceptance items for CAI-232 — how a defect points at the fix item that
will close it.

Two plain columns on `defects`, reusing `agent_task_links`' resolution
semantics but not its cache. The two sides measure different axes:
`defects.status` answers *is this bug still real*, the tracker item's state
answers *is this work item still scheduled*. regin never writes either side
from the other — and the decisive reason it cannot auto-close a defect from a
Done sub-issue is that `store.close()` requires `missing_check`, which a state
transition cannot supply.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from cli.commands.defects import defects_app
from lib.defects import store

runner = CliRunner()
KEY = "CAI-242"


def test_the_fix_item_pointer_is_set_by_an_explicit_verb_not_by_defects_open(
        no_tracker):
    """Most defects are fixed inside the session that found them, so
    auto-creation would flood the board with items that are Done before anyone
    reads them."""
    runner.invoke(defects_app, ["open", "it broke", "--feature", KEY])
    row = store.for_feature(KEY)[0]
    assert row.fix_external_key is None

    result = runner.invoke(defects_app, ["fix-item", str(row.id),
                                         "--issue", "CAI-999"])

    assert result.exit_code == 0, result.output
    assert store.get(row.id).fix_external_key == "CAI-999"
    assert store.get(row.id).fix_provider == "linear"


def test_a_defect_has_at_most_one_fix_item(no_tracker):
    """"The fix landed in two places" is a property of the fix, not evidence
    of two defects — two defects would have two symptoms and two rows."""
    from lib.orm.models.defects import Defect

    row = store.record("it broke", feature_key=KEY)
    store.set_fix_item(row.id, "linear", "CAI-1")
    store.set_fix_item(row.id, "linear", "CAI-2")

    assert store.get(row.id).fix_external_key == "CAI-2"
    assert "fix_external_key" in Defect.model_fields
    assert "fix_external_keys" not in Defect.model_fields


def test_one_fix_item_may_serve_many_defects(no_tracker):
    """The many-to-one direction is how batch cleanup actually happens — and
    the reverse lookup is the query the batch-fix case is for."""
    a = store.record("first", session_id="t-1")
    b = store.record("second", session_id="t-2")
    store.set_fix_item(a.id, "linear", "CAI-241")
    store.set_fix_item(b.id, "linear", "CAI-241")

    found = store.by_fix_item("linear", "CAI-241")

    assert sorted(r.id for r in found) == sorted([a.id, b.id])


def test_closing_a_defect_never_touches_the_fix_item(no_tracker):
    row = store.record("it broke", feature_key=KEY)
    store.set_fix_item(row.id, "linear", "CAI-999")

    store.close(row.id, missing_check="a regression test")

    assert store.get(row.id).fix_external_key == "CAI-999"


def test_closing_the_fix_item_never_closes_the_defect(tracker_issue):
    """An auto-close would fabricate `missing_check` — the one output of this
    ledger that changes future behaviour rather than describing past
    behaviour."""
    tracker_issue(KEY, state_category="started")
    tracker_issue("CAI-999", state_category="completed")
    row = store.record("it broke", feature_key=KEY)
    store.set_fix_item(row.id, "linear", "CAI-999")

    states = store.fix_states([store.get(row.id)])

    assert states[row.id] == "completed"
    assert store.get(row.id).status == "open"
    assert store.get(row.id).missing_check is None


def test_with_fix_state_reads_unknown_when_no_tracker_is_reachable(no_tracker):
    """The same refusal that keeps `tracker.rounds_for` from returning `1`
    when it could not look."""
    row = store.record("it broke", feature_key=KEY)
    store.set_fix_item(row.id, "linear", "CAI-999")

    output = runner.invoke(defects_app, ["list", "--feature", KEY,
                                         "--with-fix-state"]).output

    assert "unknown" in output


def test_with_fix_state_never_reports_in_sync(tracker_issue):
    """Divergence is surfaced, never reconciled — and "in sync" is a claim
    about two axes that do not measure the same thing."""
    tracker_issue(KEY, state_category="started")
    tracker_issue("CAI-999", state_category="completed")
    row = store.record("it broke", feature_key=KEY)
    store.set_fix_item(row.id, "linear", "CAI-999")

    output = runner.invoke(defects_app, ["list", "--feature", KEY,
                                         "--with-fix-state"]).output

    assert "in sync" not in output
    assert "CAI-999" in output


def test_create_refuses_on_an_unattributed_defect(no_tracker):
    """A parentless issue is a work item no feature owns — exactly the orphan
    late-binding attribution exists to avoid."""
    row = store.record("it broke", session_id="t-1")

    result = runner.invoke(defects_app, ["fix-item", str(row.id), "--create"])

    assert result.exit_code != 0
    assert "regin defects attribute" in result.output
    assert store.get(row.id).fix_external_key is None


def test_create_writes_the_symptom_verbatim_with_the_feature_as_parent(
        tracker_issue, monkeypatch):
    """Paraphrasing would make the board and the ledger describe the same bug
    differently. No label, no assignee, no priority — tracker fields with
    human writers."""
    tracker_issue(KEY, state_category="started")
    created: list = []

    from lib.tasks.integrations.base import ExternalRef

    def fake_create(self, *, parent, title, body):
        created.append({"parent": parent.key, "title": title, "body": body})
        return ExternalRef(provider="linear", key="CAI-500",
                           url="https://x/CAI-500")

    import lib.tasks.integrations.base as base_mod
    monkeypatch.setattr(base_mod.IssueTrackerProvider, "create_issue",
                        fake_create, raising=False)

    row = store.record("the gutter folds the newest span", feature_key=KEY,
                       session_id="t-1", suspect_paths=["a.py"])
    result = runner.invoke(defects_app, ["fix-item", str(row.id), "--create"])

    assert result.exit_code == 0, result.output
    assert created[0]["title"] == "the gutter folds the newest span"
    assert created[0]["parent"] == KEY
    assert f"regin defect #{row.id}" in created[0]["body"]
    assert "t-1" in created[0]["body"] and "a.py" in created[0]["body"]
    assert store.get(row.id).fix_external_key == "CAI-500"


def test_the_fix_item_pointer_is_last_writer_wins(no_tracker):
    """`is_primary` is first-writer-wins because cost attribution is a claim
    about the past; a fix item points at future work and legitimately moves.
    Nothing is counted from it, so a rewrite corrupts no metric."""
    row = store.record("it broke", feature_key=KEY)

    store.set_fix_item(row.id, "linear", "CAI-1")
    store.set_fix_item(row.id, "linear", "CAI-2")
    store.clear_fix_item(row.id)

    assert store.get(row.id).fix_external_key is None
    assert store.get(row.id).fix_provider is None


def test_fix_item_state_never_feeds_rounds_or_feature_stage(tracker_issue):
    """A fix item's own completion churn is work-item noise; counting it would
    inflate a feature diligent enough to schedule its fixes and exonerate one
    that patched silently."""
    from lib.defects import stage
    tracker_issue(KEY, state_category="completed", rounds=1)
    tracker_issue("CAI-999", state_category="completed", rounds=7)
    row = store.record("an escape", feature_key=KEY)
    store.set_fix_item(row.id, "linear", "CAI-999")

    result = stage.stage_for_key(KEY)

    assert result.rounds == 1
    assert result.escapes == 1
    assert "fix" not in " ".join(result.to_dict())


def test_create_on_a_native_feature_files_a_child_task_and_attaches_it(
        native_task, monkeypatch):
    """The one write the pipeline makes to a tracker, on regin's own tracker:
    the child task is a run of the feature, so its sessions and cost keep
    rolling up to `regin:<N>` with no change to the attribution model."""
    from lib.orm import SessionLocal
    from lib.orm.models.tasks import AgentTask

    from lib.settings import settings
    monkeypatch.setattr(settings.task_integrations, "linear_enabled", False)
    parent = native_task(title="the feature")
    row = store.record("it broke", feature_key=f"regin:{parent}")

    result = runner.invoke(defects_app, ["fix-item", str(row.id), "--create"])

    assert result.exit_code == 0, result.output
    saved = store.get(row.id)
    assert saved.fix_provider == "regin"
    child_id = int(saved.fix_external_key.split(":")[1])
    with SessionLocal() as session:
        child = session.get(AgentTask, child_id)
    assert child.parent_id == parent
    assert child.title == "it broke"
