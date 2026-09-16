"""Acceptance items for CAI-241 — two functions named `rounds`, one survives.

`rounds-to-done` asks *how many times was this feature called done and wasn't*.
Only a declared state transition witnesses a completion, so only the tracker
can answer it. A defect reopen witnesses nothing about done-ness: the ledger
reading could read 4 on a feature that never shipped and 1 on a feature shipped
and walked back twice. It was not a proxy, nor a floor — it was an orthogonal
axis wearing the name.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from cli.commands.defects import defects_app
from lib.defects import store

runner = CliRunner()
KEY = "CAI-242"


def test_rounds_to_done_is_the_tracker_reading_only(tracker_issue):
    from lib.defects import tracker
    tracker_issue(KEY, rounds=3)

    assert tracker.rounds_for(KEY) == 3


def test_no_feature_level_aggregate_of_defect_round_exists():
    """`fix_attempts(feature_key)` was the leaning and is rejected: it ships a
    feature-level aggregate nobody asked for. The live fact is already carried
    per row by the `round=` column in `defects list`."""
    assert not hasattr(store, "rounds")
    assert not hasattr(store, "fix_attempts")


def test_defects_list_prints_no_rounds_to_done_line(no_tracker):
    """Swapping in `tracker.rounds_for()` would put a refusable network fetch
    in the tail of a local list command. `regin defects rounds <KEY>` is
    already the verb for that number."""
    store.record("one", feature_key=KEY)

    output = runner.invoke(defects_app, ["list", "--feature", KEY]).output

    assert "rounds-to-done" not in output
    assert "round=1" in output


def test_reopen_increments_the_round_and_clears_missing_check(no_tracker):
    """Reopening *is* the evidence the named check did not catch the bug, so
    leaving the string standing means the ledger holds a claim it has itself
    disproven."""
    row = store.record("it broke", feature_key=KEY)
    store.close(row.id, missing_check="a test that never ran")

    result = runner.invoke(defects_app, ["reopen", str(row.id)])

    assert result.exit_code == 0, result.output
    after = store.get(row.id)
    assert after.round == 2
    assert after.missing_check is None
    assert after.status == "open"


def test_missing_check_is_non_null_if_and_only_if_the_defect_is_closed(
        no_tracker):
    row = store.record("it broke", feature_key=KEY)
    assert (store.get(row.id).missing_check is None) is True

    store.close(row.id, missing_check="a regression test")
    assert store.get(row.id).missing_check is not None

    store.reopen(row.id)
    assert store.get(row.id).missing_check is None


def test_reopen_refuses_non_zero_on_an_already_open_defect(no_tracker):
    """Incrementing `round` on a row that was never closed records a
    walk-back that never happened, inflating the rework figure from a re-typed
    command."""
    row = store.record("it broke", feature_key=KEY)

    result = runner.invoke(defects_app, ["reopen", str(row.id)])

    assert result.exit_code != 0
    assert "already open" in result.output
    assert store.get(row.id).round == 1
    with pytest.raises(store.DefectError):
        store.reopen(row.id)
