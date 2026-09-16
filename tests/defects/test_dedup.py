"""Acceptance items for CAI-240 — deduplicating unattributed defects.

`regin defects open` is run by an **agent**, so a retried command after a
transient failure becomes a second row nobody notices at ten rows a quarter.
Inside one trace that is a duplicate; across two traces it is two independent
reports, and the existing "two vague reports are not evidence they describe the
same bug" still holds there.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from cli.commands.defects import defects_app
from lib.defects import store

runner = CliRunner()
KEY = "CAI-242"


def test_an_unattributed_defect_dedups_within_its_session():
    store.record("the composer drops its draft", session_id="t-1")
    store.record("The Composer   drops its draft", session_id="t-1")

    assert len(store.unattributed()) == 1


def test_the_same_symptom_in_two_sessions_is_two_defects():
    store.record("the composer drops its draft", session_id="t-1")
    store.record("the composer drops its draft", session_id="t-2")

    assert len(store.unattributed()) == 2


def test_a_row_with_neither_feature_nor_session_is_always_distinct():
    """With no feature and no session there is nothing to collide with, and
    the row is still a good record: somebody saw something break."""
    store.record("something is off somewhere")
    result = runner.invoke(defects_app, ["open", "something is off somewhere"])

    assert result.exit_code == 0, result.output
    assert len(store.unattributed()) == 2
    assert "already open" not in result.output


def test_attribute_refuses_on_a_unique_collision_and_names_merge(no_tracker):
    """Two *different* sessions report the same symptom, and both are later
    attributed to the same feature. Letting it through inflates
    defects-per-feature, the metric this ledger exists for."""
    first = store.record("the gutter folds", session_id="t-1")
    second = store.record("the gutter folds", session_id="t-2")
    store.attribute(first.id, KEY)

    with pytest.raises(store.DefectError) as exc:
        store.attribute(second.id, KEY)

    assert f"#{first.id}" in str(exc.value)
    assert "merge" in str(exc.value)


def test_merge_deletes_the_duplicate_and_unions_its_suspect_paths(no_tracker):
    keeper = store.record("a", session_id="t-1", suspect_paths=["a.py"])
    dup = store.record("b", session_id="t-2", suspect_paths=["b.py", "a.py"])

    store.merge(dup.id, into=keeper.id)

    rows = store.unattributed()
    assert [r.id for r in rows] == [keeper.id]
    assert sorted(json.loads(rows[0].suspect_paths_json)) == ["a.py", "b.py"]


def test_merge_refuses_a_self_merge(no_tracker):
    row = store.record("a", session_id="t-1")

    with pytest.raises(store.DefectError):
        store.merge(row.id, into=row.id)


def test_merge_refuses_two_different_fix_items(no_tracker):
    """Two scheduled work items means somebody thought these were two bugs,
    and silently dropping a board pointer is not a merge's business."""
    keeper = store.record("a", session_id="t-1")
    dup = store.record("b", session_id="t-2")
    store.set_fix_item(keeper.id, "linear", "CAI-1")
    store.set_fix_item(dup.id, "linear", "CAI-2")

    with pytest.raises(store.DefectError):
        store.merge(dup.id, into=keeper.id)

    # But if only the duplicate has one, the keeper adopts it.
    store.clear_fix_item(keeper.id)
    store.merge(dup.id, into=keeper.id)
    assert store.get(keeper.id).fix_external_key == "CAI-2"


def test_a_dedup_hit_says_so_and_never_touches_round():
    """Silence is the wrong default when the caller is an agent: a silent
    no-op read as a successful open is how a session ends up believing it
    filed something it did not."""
    runner.invoke(defects_app, ["open", "same thing", "--session", "t-1",
                                "--path", "a.py"])
    again = runner.invoke(defects_app, ["open", "same thing", "--session",
                                        "t-1", "--path", "b.py"])

    row = store.unattributed()[0]
    assert again.exit_code == 0, again.output
    assert "already open" in again.output
    assert row.round == 1
    assert sorted(json.loads(row.suspect_paths_json)) == ["a.py", "b.py"]


def test_a_dedup_hit_on_a_closed_row_refuses_and_points_at_reopen():
    """Re-reporting a fixed bug is the definition of a walked-back completion,
    but auto-reopening would let the rework metric increment from a re-typed
    string — the inference-from-prose this pipeline deleted once already."""
    first = runner.invoke(defects_app, ["open", "same thing",
                                        "--session", "t-1"])
    assert first.exit_code == 0
    row = store.unattributed()[0]
    store.close(row.id, missing_check="a regression test")

    again = runner.invoke(defects_app, ["open", "same thing",
                                        "--session", "t-1"])

    assert again.exit_code != 0
    assert f"reopen {row.id}" in again.output
    assert store.get(row.id).round == 1
