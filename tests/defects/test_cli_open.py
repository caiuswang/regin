"""`regin defects open` — the command the routine starts from.

Recording a defect had no CLI, so every one had to be written through inline
Python. A ledger nobody can add to from a terminal is a ledger that stays
empty, which is the failure the whole design exists to avoid.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.commands.defects import defects_app
from lib.defects import store

runner = CliRunner()


def test_open_records_a_defect_against_a_feature(feature_row):
    feature = feature_row()

    result = runner.invoke(defects_app, [
        "open", "the gutter folds the newest span",
        "--feature", str(feature.id)])

    assert result.exit_code == 0
    rows = store.for_feature(feature.id)
    assert [r.symptom for r in rows] == ["the gutter folds the newest span"]


def test_open_by_ref_names_the_feature_carrying_that_reference(feature_row):
    """`--ref` is the id spelled as a tracker identifier, for the case where
    that is what the operator has in front of them. It resolves to the one
    feature row holding it and refuses rather than minting a second."""
    feature = feature_row(origin="linear", external_ref="CAI-274")

    result = runner.invoke(defects_app, [
        "open", "it leaks", "--ref", "CAI-274"])

    assert result.exit_code == 0, result.output
    assert [r.symptom for r in store.for_feature(feature.id)] == ["it leaks"]


def test_open_by_ref_refuses_a_reference_no_feature_carries(no_tracker):
    """A typo would otherwise mint a second feature that looks like the first
    — corrupted data that looks fine."""
    result = runner.invoke(defects_app, ["open", "it leaks",
                                         "--ref", "CAI-999"])

    assert result.exit_code != 0
    assert store.every() == []


def test_open_without_a_feature_records_it_unattributed():
    result = runner.invoke(defects_app, ["open", "something is off somewhere"])

    assert result.exit_code == 0
    assert len(store.unattributed()) == 1


def test_open_carries_the_session_and_suspect_paths(feature_row):
    feature = feature_row()

    runner.invoke(defects_app, [
        "open", "the composer drops its draft", "--feature", str(feature.id),
        "--session", "trace-xyz", "--path", "a.py", "--path", "b.vue"])

    row = store.for_feature(feature.id)[0]
    assert row.reported_by_trace == "trace-xyz"
    assert json.loads(row.suspect_paths_json) == ["a.py", "b.vue"]


def test_opening_the_same_symptom_twice_reports_the_same_record(feature_row):
    feature = feature_row()

    first = runner.invoke(defects_app, ["open", "same thing",
                                        "--feature", str(feature.id)])
    again = runner.invoke(defects_app, ["open", "same thing",
                                        "--feature", str(feature.id)])

    assert first.exit_code == again.exit_code == 0
    assert len(store.for_feature(feature.id)) == 1


def test_list_all_shows_attributed_and_unattributed_with_their_features(
        feature_row):
    """`--all` mirrors the web's default scope. Without it the terminal had
    the same blind spot the page did: a defect blamed at report time appeared
    in no list either surface offered."""
    feature = feature_row("the ledger")
    store.record("blamed on a feature", feature_id=feature.id)
    store.record("blamed on nobody", session_id="t-1")

    every = runner.invoke(defects_app, ["list", "--all"])
    loose = runner.invoke(defects_app, ["list"])

    assert every.exit_code == 0
    assert "blamed on a feature" in every.stdout
    assert "blamed on nobody" in every.stdout
    assert "the ledger" in every.stdout and "unattributed" in every.stdout
    assert "blamed on a feature" not in loose.stdout


def test_list_all_on_an_empty_ledger_says_so_without_claiming_attribution():
    result = runner.invoke(defects_app, ["list", "--all"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "no defects"


def test_list_fix_item_finds_every_defect_one_task_will_close(native_task):
    """A task id rather than the `(provider, key)` pair it replaces: the loop
    closes in the schema, so a query joins instead of re-parsing a string —
    and every spelling the write accepted read back the same way."""
    task_id = native_task(title="fix it")
    row = store.record("one", session_id="t-1")
    runner.invoke(defects_app, ["fix-item", str(row.id), "--task",
                                str(task_id)])

    result = runner.invoke(defects_app, ["list", "--fix-item", str(task_id)])

    assert f"#{row.id}" in result.stdout


def test_an_empty_fix_item_scope_does_not_describe_a_different_one():
    """It used to say "awaiting attribution" — a sentence about the
    unattributed scope, which is not the scope that was asked for."""
    result = runner.invoke(defects_app, ["list", "--fix-item", "999"])

    assert result.exit_code == 0
    assert "awaiting attribution" not in result.stdout
    assert "999" in result.stdout


def test_a_feature_argument_that_is_not_an_id_is_refused_by_name():
    """The padded-key normalisation this replaced existed because the key was
    identity and `' CAI-206'` was a second feature. An integer has no padding
    to strip — and a tracker reference typed into `--feature` is told which
    flag it belongs to."""
    result = runner.invoke(defects_app, ["open", "padded",
                                         "--feature", " CAI-206 "])

    assert result.exit_code != 0
    assert "--ref" in result.output
