"""Two functions named `rounds`, one survives.

`rounds-to-done` asks *how many times was this feature called done and wasn't*.
Only a declared state transition witnesses a completion, so it is a count of
`restarted` events. A defect reopen witnesses nothing about done-ness: the
ledger reading could read 4 on a feature that never shipped and 1 on a feature
shipped and walked back twice. It was not a proxy, nor a floor — it was an
orthogonal axis wearing the name.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from cli.commands.defects import defects_app
from lib.defects import store

runner = CliRunner()


def test_rounds_to_done_is_the_event_log_only(feature_row):
    """It used to be a tracker fetch, which could refuse — so `1` had to be
    forbidden from doubling as "we could not look". One local source, one
    reading."""
    from lib.features import events

    feature = feature_row(stage=events.SHIPPED)
    events.append(feature.id, events.RESTARTED, declared_by=events.OPERATOR)
    events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)
    events.append(feature.id, events.RESTARTED, declared_by=events.OPERATOR)

    assert events.rounds(feature.id) == 3


def test_no_feature_level_aggregate_of_defect_round_exists():
    """`fix_attempts(feature_key)` was the leaning and is rejected: it ships a
    feature-level aggregate nobody asked for. The live fact is already carried
    per row by the `round=` column in `defects list`."""
    assert not hasattr(store, "rounds")
    assert not hasattr(store, "fix_attempts")


def test_defects_list_prints_no_rounds_to_done_line(feature_row):
    """A feature-level figure in the tail of a per-defect list invites the two
    numbers to be read as one. `regin defects rounds <ID>` is already the verb
    for that one."""
    feature = feature_row()
    store.record("one", feature_id=feature.id)

    output = runner.invoke(defects_app,
                           ["list", "--feature", str(feature.id)]).output

    assert "rounds-to-done" not in output
    assert "round=1" in output


def test_reopen_increments_the_round_and_clears_missing_check(feature_row):
    """Reopening *is* the evidence the named check did not catch the bug, so
    leaving the string standing means the ledger holds a claim it has itself
    disproven."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)
    store.close(row.id, missing_check="a test that never ran")

    result = runner.invoke(defects_app, ["reopen", str(row.id)])

    assert result.exit_code == 0, result.output
    after = store.get(row.id)
    assert after.round == 2
    assert after.missing_check is None
    assert after.status == "open"


def test_missing_check_is_non_null_if_and_only_if_the_defect_is_closed(
        feature_row):
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)
    assert (store.get(row.id).missing_check is None) is True

    store.close(row.id, missing_check="a regression test")
    assert store.get(row.id).missing_check is not None

    store.reopen(row.id)
    assert store.get(row.id).missing_check is None


def test_reopen_refuses_non_zero_on_an_already_open_defect(feature_row):
    """Incrementing `round` on a row that was never closed records a
    walk-back that never happened, inflating the rework figure from a re-typed
    command."""
    feature = feature_row()
    row = store.record("it broke", feature_id=feature.id)

    result = runner.invoke(defects_app, ["reopen", str(row.id)])

    assert result.exit_code != 0
    assert "already open" in result.output
    assert store.get(row.id).round == 1
    with pytest.raises(store.DefectError):
        store.reopen(row.id)
