"""The reverse lookup a trace view reads.

`sessions_for_feature` merges declared rows with the run lane; the reverse
direction did not, and on the live database that gap covered 22 of 23
attributed traces. These pin the merged reader and, just as importantly, pin
that the blame lookup underneath `@session` did not move with it.
"""

from __future__ import annotations

import pytest

from lib.features import attribution


def test_merged_lookup_includes_run_lane_traces(session_row, task_lane,
                                                feature_row):
    feature = feature_row()
    session_row("t-lane")
    task_lane(801, feature.id, [("t-lane", "done")])

    rows = attribution.attributions_for_session("t-lane")

    assert [(r.feature_id, r.provenance) for r in rows] == [
        (feature.id, attribution.RUN)]


def test_merged_lookup_includes_declared_rows(session_row, feature_row):
    feature = feature_row()
    session_row("t-declared")
    attribution.attribute(feature.id, "t-declared")

    rows = attribution.attributions_for_session("t-declared")

    assert [(r.feature_id, r.provenance) for r in rows] == [
        (feature.id, attribution.RECALLED)]


def test_a_declared_row_wins_over_the_same_feature_in_the_run_lane(
        session_row, task_lane, feature_row):
    feature = feature_row()
    session_row("t-both")
    task_lane(802, feature.id, [("t-both", "done")])
    attribution.attribute(feature.id, "t-both", primary=True)

    rows = attribution.attributions_for_session("t-both")

    assert len(rows) == 1
    assert rows[0].provenance == attribution.RECALLED
    assert rows[0].is_primary == 1


def test_merged_lookup_carries_the_task_id_for_a_run_lane_row(
        session_row, task_lane, feature_row):
    feature = feature_row()
    session_row("t-tagged")
    task_lane(803, feature.id, [("t-tagged", "done")])

    row = attribution.attributions_for_session("t-tagged")[0]

    assert row.task_id == 803


def test_a_declared_row_carries_no_task_id(session_row, feature_row):
    feature = feature_row()
    session_row("t-plain")
    attribution.attribute(feature.id, "t-plain")

    assert attribution.attributions_for_session("t-plain")[0].task_id is None


def test_features_for_session_is_unchanged(session_row, task_lane,
                                           feature_row):
    """The blame lookup stays declarations-only: `--feature @session` demands
    an explicit claim, and widening it here would change what the shipped CLI
    accepts."""
    feature = feature_row()
    session_row("t-lane-only")
    task_lane(804, feature.id, [("t-lane-only", "done")])

    assert attribution.features_for_session("t-lane-only") == []


def test_declared_feature_is_unchanged(session_row, task_lane, feature_row):
    feature = feature_row()
    session_row("t-lane-blame")
    task_lane(805, feature.id, [("t-lane-blame", "done")])

    with pytest.raises(attribution.NoDeclaration):
        attribution.declared_feature("t-lane-blame")


def test_trace_view_and_feature_page_agree_on_every_attributed_trace(
        session_row, task_lane, feature_row):
    """The disagreement this function exists to close, stated as a property."""
    feature = feature_row()
    session_row("t-a")
    session_row("t-b")
    task_lane(806, feature.id, [("t-a", "done")])
    attribution.attribute(feature.id, "t-b")

    forward = {row.trace_id for row in
               attribution.sessions_for_feature(feature.id)}
    reverse = {trace for trace in forward
               if any(r.feature_id == feature.id
                      for r in attribution.attributions_for_session(trace))}

    assert reverse == forward


def test_an_unattributed_session_has_no_rows(session_row):
    session_row("t-none")

    assert attribution.attributions_for_session("t-none") == []


def test_a_task_under_no_feature_contributes_no_row(session_row, task_lane):
    """The ambiguity guard this replaced refused a task holding two feature
    keys. A task holds one column, so the only remaining state is holding
    none — which is a real state, and contributes nothing."""
    session_row("t-unfeatured")
    task_lane(807, None, [("t-unfeatured", "done")])

    assert attribution.attributions_for_session("t-unfeatured") == []
