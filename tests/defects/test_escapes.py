"""Acceptance items for CAI-233 — post-ship rework, read without a clock.

An **escape** is a defect blamed on a feature that had already shipped. The
reading exists in this exact shape to avoid comparing `store._now()` on regin's
host against `stateHistory.startedAt` as recorded by Linear's servers: a defect
opened near the ship boundary would fall on either side of it according to host
drift, and nothing in the data would say which. Both operands are therefore
declared at a single instant instead — a human asserts *X caused this*, and the
tracker asserts *X is completed*.
"""

from __future__ import annotations

import inspect

from typer.testing import CliRunner

from cli.commands.defects import defects_app
from lib.defects import escapes, stage, store

runner = CliRunner()
KEY = "CAI-242"


def test_blamed_against_state_is_written_once_when_feature_key_is_first_set(
        tracker_issue):
    tracker_issue(KEY, state_category="completed")
    row = store.record("it leaks", feature_key=KEY)
    assert row.blamed_against_state == "completed"

    tracker_issue(KEY, state_category="started")
    store.attribute(row.id, KEY)

    assert store.for_feature(KEY)[0].blamed_against_state == "completed"


def test_blamed_against_state_stores_the_raw_state_category(tracker_issue):
    """Not the derived stage name: `stage.py`'s names are a *reading* of the
    category, and freezing today's reading into the row would silently
    reinterpret history if the reading ever changes."""
    tracker_issue(KEY, state_category="completed")

    row = store.record("it leaks", feature_key=KEY)

    assert row.blamed_against_state == "completed"
    assert row.blamed_against_state not in (stage.SHIPPED, stage.ESCAPED)


def test_late_attribution_binds_blamed_against_state(tracker_issue):
    """Snapshotting only at `record()` would leave the column NULL for the
    majority of defects, because CAI-238 made late attribution the sanctioned
    path."""
    tracker_issue(KEY, state_category="completed")
    row = store.record("blamed later", session_id="t-x")
    assert row.blamed_against_state is None

    store.attribute(row.id, KEY)

    assert store.for_feature(KEY)[0].blamed_against_state == "completed"


def test_a_failed_tracker_fetch_writes_null_and_still_lands_the_defect(
        no_tracker):
    """Losing a defect report to a network blip corrupts the ledger the metric
    reads — that is worse than losing one field."""
    row = store.record("landed anyway", feature_key=KEY)

    assert row.id is not None
    assert row.blamed_against_state is None
    assert [r.symptom for r in store.for_feature(KEY)] == ["landed anyway"]


def test_null_blamed_against_state_is_a_counted_gap_not_a_non_escape(
        no_tracker):
    store.record("we never looked", feature_key=KEY)

    counts = escapes.counts_for(KEY)

    assert counts.escapes == 0
    assert counts.state_unknown == 1


def test_unknown_vocabulary_is_distinct_from_never_looked(tracker_issue):
    """`_category()` maps a tracker vocabulary regin does not carry to the
    string `"unknown"` — *we looked, and the answer was a word we don't hold*.
    NULL means *we never looked*. Neither is folded into the count."""
    store.record("never looked", feature_key=KEY)
    tracker_issue(KEY, state_category="unknown")
    store.record("looked, got a word we do not carry", feature_key=KEY)

    counts = escapes.counts_for(KEY)

    assert counts.state_unknown == 1
    assert counts.unknown_vocabulary == 1
    assert counts.escapes == 0


def test_escapes_are_reported_beside_rounds_and_never_folded_into_it(
        tracker_issue):
    tracker_issue(KEY, state_category="completed", rounds=1)
    store.record("an escape", feature_key=KEY)

    result = stage.stage_for_key(KEY)

    assert result.escapes == 1
    assert result.rounds == 1
    assert "escapes" in result.to_dict() and "rounds" in result.to_dict()


def test_filing_a_defect_never_reopens_the_feature(tracker_issue, monkeypatch):
    """A board that flaps is a board nobody trusts. Post-ship rework is read
    from the defects ledger, never written back to the tracker."""
    tracker_issue(KEY, state_category="completed")
    writes: list = []
    import lib.tasks.integrations.linear as linear_mod
    monkeypatch.setattr(linear_mod.LinearProvider, "_post",
                        lambda self, body: writes.append(body) or {"data": {}})

    runner.invoke(defects_app, ["open", "it leaks", "--feature", KEY])

    assert writes == []
    assert stage.stage_for_key(KEY).name == stage.ESCAPED


def test_no_timestamp_comparison_decides_an_escape():
    """The operands are two clocks on two machines. The whole module is read
    for the absence of the subtraction."""
    source = inspect.getsource(escapes)

    for forbidden in ("opened_at", "startedAt", "started_at", "completedAt",
                      "datetime", "timedelta", "_now("):
        assert forbidden not in source, forbidden
    assert escapes.is_escape("completed") is True
    assert escapes.is_escape("started") is False
    assert escapes.is_escape(None) is False
