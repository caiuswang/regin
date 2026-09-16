"""Post-ship rework, read without a clock.

An **escape** is a defect blamed on a feature that had already shipped. The
reading exists in this exact shape to avoid comparing `store._now()` on regin's
host against a remote tracker's own timestamps: a defect opened near the ship
boundary would fall on either side of it according to host drift, and nothing
in the data would say which. Both operands are therefore fixed at a single
instant instead — a human asserts *X caused this*, and the defect is pointed at
whatever event X's log currently ends on.

That pointer replaced a cached state *word*, and with it the two gap counters
the word needed. A string could be absent (nobody looked), stale, or a
vocabulary regin did not carry; an event is none of those, so `counts_for`
reports a count and nothing else.
"""

from __future__ import annotations

import inspect

from typer.testing import CliRunner

from cli.commands.defects import defects_app
from lib.defects import escapes, store
from lib.features import events

runner = CliRunner()


def test_the_blame_pointer_is_written_once_per_blame(feature_row):
    """Re-declaring the same feature is not a new blame, so the snapshot does
    not move — and neither does the feature's later history."""
    feature = feature_row(stage=events.SHIPPED)
    row = store.record("it leaks", feature_id=feature.id)
    pointed_at = row.blamed_event_id

    events.append(feature.id, events.RESTARTED, declared_by=events.OPERATOR)
    store.attribute(row.id, feature.id)

    assert store.for_feature(feature.id)[0].blamed_event_id == pointed_at


def test_the_pointer_names_an_event_not_a_state_word(feature_row):
    """A stage name is a *reading* of a state, and freezing today's reading
    into the row would silently reinterpret history if the reading changed.
    An event id names the thing itself."""
    feature = feature_row(stage=events.SHIPPED)

    row = store.record("it leaks", feature_id=feature.id)

    assert row.blamed_event_id == events.last(feature.id).id
    assert not hasattr(row, "blamed_against_state")


def test_late_attribution_binds_the_pointer(feature_row):
    """Snapshotting only at `record()` would leave the column NULL for the
    majority of defects, because late attribution is the sanctioned path."""
    feature = feature_row(stage=events.SHIPPED)
    row = store.record("blamed later", session_id="t-x")
    assert row.blamed_event_id is None

    store.attribute(row.id, feature.id)

    assert escapes.counts_for(feature.id).escapes == 1


def test_blaming_cannot_fail_the_way_a_fetch_could(feature_row, no_tracker):
    """The pointer is a local row that already exists, so there is no network
    blip that can lose the field — the failure the old NULL-and-land-anyway
    behaviour existed to survive."""
    feature = feature_row(stage=events.SHIPPED)

    row = store.record("landed anyway", feature_id=feature.id)

    assert row.blamed_event_id is not None
    assert [r.symptom for r in store.for_feature(feature.id)] == [
        "landed anyway"]


def test_escape_counts_carry_no_gap_counters(feature_row):
    """`state_unknown` and `unknown_vocabulary` described the readings a
    cached string could have. There is one reading now, so there is one
    field."""
    feature = feature_row(stage=events.SHIPPED)
    store.record("an escape", feature_id=feature.id)

    counts = escapes.counts_for(feature.id)

    assert counts.escapes == 1
    assert [f for f in vars(counts)] == ["escapes"]


def test_escapes_are_reported_beside_rounds_and_never_folded_into_it(
        feature_row):
    from lib.features import ledger as ledger_module

    feature = feature_row(stage=events.SHIPPED)
    store.record("an escape", feature_id=feature.id)

    report = ledger_module.build(feature.id)

    assert report.escapes.escapes == 1
    assert report.rounds == 1
    assert report.stage == events.STAGE_ESCAPED


def test_filing_a_defect_never_walks_the_ship_back(feature_row):
    """A board that flaps is a board nobody trusts. Post-ship rework is read
    from the defect ledger, never written back as a lifecycle event."""
    feature = feature_row(stage=events.SHIPPED)

    runner.invoke(defects_app, ["open", "it leaks",
                                "--feature", str(feature.id)])

    assert events.last(feature.id).kind == events.SHIPPED
    assert events.rounds(feature.id) == 1
    assert events.stage(feature.id) == events.STAGE_ESCAPED


def test_no_timestamp_comparison_decides_an_escape():
    """The operands would be two clocks on two machines. The whole module is
    read for the absence of the subtraction."""
    source = inspect.getsource(escapes)

    for forbidden in ("opened_at", "startedAt", "started_at", "completedAt",
                      "datetime", "timedelta", "_now("):
        assert forbidden not in source, forbidden
