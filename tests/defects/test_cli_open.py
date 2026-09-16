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


def test_open_records_a_defect_against_a_feature():
    result = runner.invoke(defects_app, [
        "open", "the gutter folds the newest span", "--feature", "CAI-206"])

    assert result.exit_code == 0
    rows = store.for_feature("CAI-206")
    assert [r.symptom for r in rows] == ["the gutter folds the newest span"]


def test_open_without_a_feature_records_it_unattributed():
    result = runner.invoke(defects_app, ["open", "something is off somewhere"])

    assert result.exit_code == 0
    assert len(store.unattributed()) == 1


def test_open_carries_the_session_and_suspect_paths():
    runner.invoke(defects_app, [
        "open", "the composer drops its draft", "--feature", "CAI-206",
        "--session", "trace-xyz", "--path", "a.py", "--path", "b.vue"])

    row = store.for_feature("CAI-206")[0]
    assert row.session_id == "trace-xyz"
    assert json.loads(row.suspect_paths_json) == ["a.py", "b.vue"]


def test_opening_the_same_symptom_twice_reports_the_same_record():
    first = runner.invoke(defects_app, ["open", "same thing", "--feature", "CAI-206"])
    again = runner.invoke(defects_app, ["open", "same thing", "--feature", "CAI-206"])

    assert first.exit_code == again.exit_code == 0
    assert len(store.for_feature("CAI-206")) == 1


def test_list_all_shows_attributed_and_unattributed_with_their_keys():
    """`--all` mirrors the web's default scope. Without it the terminal had
    the same blind spot the page did: a defect blamed at report time appeared
    in no list either surface offered."""
    store.record("blamed on a feature", feature_key="CAI-206")
    store.record("blamed on nobody", session_id="t-1")

    every = runner.invoke(defects_app, ["list", "--all"])
    loose = runner.invoke(defects_app, ["list"])

    assert every.exit_code == 0
    assert "blamed on a feature" in every.stdout
    assert "blamed on nobody" in every.stdout
    assert "CAI-206" in every.stdout and "unattributed" in every.stdout
    assert "blamed on a feature" not in loose.stdout


def test_list_all_on_an_empty_ledger_says_so_without_claiming_attribution():
    result = runner.invoke(defects_app, ["list", "--all"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "no defects"


def test_list_fix_item_finds_a_row_by_any_spelling_the_write_accepted():
    """`--fix-item` queried the raw text while the write stored the tracker's
    canonical key, so a pasted URL read back as "nothing points at this"."""
    row = store.record("one", session_id="t-1")
    runner.invoke(defects_app, [
        "fix-item", str(row.id), "--issue",
        "https://linear.app/acme/issue/CAI-241/some-slug"])

    for spelling in ("CAI-241",
                     "https://linear.app/acme/issue/CAI-241/some-slug"):
        result = runner.invoke(defects_app, ["list", "--fix-item", spelling])
        assert f"#{row.id}" in result.stdout, spelling


def test_an_empty_fix_item_scope_does_not_describe_a_different_one():
    """It used to say "awaiting attribution" — a sentence about the
    unattributed scope, which is not the scope that was asked for."""
    result = runner.invoke(defects_app, ["list", "--fix-item", "CAI-999"])

    assert result.exit_code == 0
    assert "awaiting attribution" not in result.stdout
    assert "CAI-999" in result.stdout


def test_open_strips_a_padded_feature_key():
    runner.invoke(defects_app, ["open", "padded", "--feature", " CAI-206 "])

    assert [r.symptom for r in store.for_feature("CAI-206")] == ["padded"]
