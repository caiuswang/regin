"""The reverse lookup a trace view reads (CAI-256).

`sessions_for_feature` merges declared rows with the task lane; the reverse
direction did not, and on the live database that gap covered 22 of 23
attributed traces. These pin the merged reader and, just as importantly, pin
that the blame lookup underneath `@session` did not move with it.
"""

from __future__ import annotations

import pytest

from lib.features import attribution


def test_merged_lookup_includes_task_lane_traces(session_row, task_lane):
    session_row("t-lane")
    task_lane(801, ["CAI-900"], [("t-lane", "done")])

    rows = attribution.attributions_for_session("t-lane")

    assert [(r.feature_key, r.provenance) for r in rows] == [
        ("CAI-900", attribution.TASK_LINK)]


def test_merged_lookup_includes_declared_rows(session_row):
    session_row("t-declared")
    attribution.attribute("CAI-901", "t-declared")

    rows = attribution.attributions_for_session("t-declared")

    assert [(r.feature_key, r.provenance) for r in rows] == [
        ("CAI-901", attribution.RECALLED)]


def test_a_declared_row_wins_over_the_same_key_in_the_task_lane(
        session_row, task_lane):
    session_row("t-both")
    task_lane(802, ["CAI-902"], [("t-both", "done")])
    attribution.attribute("CAI-902", "t-both", primary=True)

    rows = attribution.attributions_for_session("t-both")

    assert len(rows) == 1
    assert rows[0].provenance == attribution.RECALLED
    assert rows[0].is_primary == 1


def test_merged_lookup_carries_the_task_id_for_a_task_lane_row(
        session_row, task_lane):
    session_row("t-tagged")
    task_lane(803, ["CAI-903"], [("t-tagged", "done")])

    row = attribution.attributions_for_session("t-tagged")[0]

    assert row.task_id == 803


def test_a_declared_row_carries_no_task_id(session_row):
    session_row("t-plain")
    attribution.attribute("CAI-904", "t-plain")

    assert attribution.attributions_for_session("t-plain")[0].task_id is None


def test_features_for_session_is_unchanged(session_row, task_lane):
    """The blame lookup stays declarations-only: `regin defects blame
    @session` demands an explicit claim, and widening it here would change
    what shipped CLI accepts."""
    session_row("t-lane-only")
    task_lane(804, ["CAI-905"], [("t-lane-only", "done")])

    assert attribution.features_for_session("t-lane-only") == []


def test_declared_feature_is_unchanged(session_row, task_lane):
    session_row("t-lane-blame")
    task_lane(805, ["CAI-906"], [("t-lane-blame", "done")])

    with pytest.raises(attribution.NoDeclaration):
        attribution.declared_feature("t-lane-blame")


def test_trace_view_and_feature_page_agree_on_every_attributed_trace(
        session_row, task_lane):
    """The disagreement this function exists to close, stated as a property."""
    session_row("t-a")
    session_row("t-b")
    task_lane(806, ["CAI-907"], [("t-a", "done")])
    attribution.attribute("CAI-907", "t-b")

    forward = {row.trace_id for row in
               attribution.sessions_for_feature("CAI-907")}
    reverse = {trace for trace in forward
               if any(r.feature_key == "CAI-907"
                      for r in attribution.attributions_for_session(trace))}

    assert reverse == forward


def test_an_unattributed_session_has_no_rows(session_row):
    session_row("t-none")

    assert attribution.attributions_for_session("t-none") == []


def test_an_ambiguous_task_link_contributes_no_row(session_row, task_lane):
    """`sessions_for_feature` refuses an ambiguous link rather than charging
    one session to two features; the reverse direction cannot quietly do what
    the forward one refuses."""
    session_row("t-ambiguous")
    task_lane(807, ["CAI-908", "CAI-909"], [("t-ambiguous", "done")])

    assert attribution.attributions_for_session("t-ambiguous") == []
