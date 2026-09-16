"""A ledger read never touches a tracker — from any surface.

The rule used to be enforced by a seam: `build()` took a `stage_resolver`, the
CLI passed a live fetch and the web passed a cache reader, because a stage read
from a stale snapshot would have changed the escape counts in the CLI's own
report. Both halves are gone. The stage is the feature's last `feature_events`
row plus a count of escapes, so there is one reading, it is local, and the
parameter that chose between two has nothing left to choose.

What survives is the property the seam existed for, asserted directly: no read
path resolves a tracker reference, for any caller.
"""

from __future__ import annotations

from lib.features import attribution, ledger as ledger_module


def _forbidden(*args, **kwargs):
    raise AssertionError("a ledger read must not reach the tracker")


def test_no_ledger_read_resolves_a_tracker_reference(session_row, task_lane,
                                                     feature_row, monkeypatch):
    from lib.tasks.integrations import registry
    monkeypatch.setattr(registry, "resolve_ref", _forbidden)
    feature = feature_row(stage="shipped")
    session_row("t-cached")
    task_lane(903, feature.id, [("t-cached", "done")])

    assert ledger_module.build(feature.id).stage == "shipped"


def test_a_feature_nobody_fetched_still_has_a_stage(feature_row):
    """`spec` was once forbidden from standing in for "nobody has looked",
    so an unfetched key read as no stage at all — and 31 of them did. There is
    nothing left to look at: every feature carries its own `specced` event."""
    feature = feature_row()

    assert ledger_module.build(feature.id).stage == "spec"


def test_no_read_calls_fix_states(session_row, task_lane, feature_row,
                                  monkeypatch):
    """It used to do network I/O per fix item. It is a local join now, and the
    ledger still does not call it: a defect's fix item is a different question
    from what the feature cost."""
    from lib.defects import store
    monkeypatch.setattr(store, "fix_states", _forbidden)
    feature = feature_row()
    session_row("t-fix")
    task_lane(906, feature.id, [("t-fix", "done")])

    ledger_module.build(feature.id)


def test_a_run_lane_row_carries_the_task_that_put_it_there(
        session_row, task_lane, feature_row):
    """The provenance tag links to `/tasks/N`, so the row has to name N.

    Asserted on a ledger `build()` actually assembled rather than on a payload
    a test wrote: the field was missing from `SessionRow` for exactly as long
    as every check for it supplied its own value.
    """
    feature = feature_row()
    session_row("t-tagged-row")
    task_lane(910, feature.id, [("t-tagged-row", "done")])

    row = ledger_module.build(feature.id).rows[0]

    assert row.provenance == attribution.RUN
    assert row.task_id == 910
    assert row.to_dict()["task_id"] == 910


def test_a_declared_row_carries_no_task_id(session_row, feature_row):
    """A tag that names a task nobody placed is a provenance nobody can
    audit."""
    feature = feature_row()
    session_row("t-plain-row")
    attribution.attribute(feature.id, "t-plain-row")

    assert ledger_module.build(feature.id).rows[0].to_dict()["task_id"] is None
