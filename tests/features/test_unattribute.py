"""Withdrawing a declaration.

`attribute` was upsert-only and there was no verb to undo it. Un-declaring has
to exist before a browser can offer it, and it leaves a span of its own
because `session_spans` is append-only — the declaration is not erased from
history, it is countered.
"""

from __future__ import annotations

import pytest

from lib.features import attribution


def test_unattribute_deletes_the_row(session_row, feature_row):
    feature = feature_row()
    session_row("t-undo")
    attribution.attribute(feature.id, "t-undo")

    assert attribution.unattribute(feature.id, "t-undo") is True
    assert attribution.features_for_session("t-undo") == []


def test_unattribute_of_an_absent_row_is_false(session_row, feature_row):
    feature = feature_row()
    session_row("t-absent")

    assert attribution.unattribute(feature.id, "t-absent") is False


def test_unattribute_leaves_other_features_of_the_session_alone(session_row,
                                                                feature_row):
    first, second = feature_row("A"), feature_row("B")
    session_row("t-two")
    attribution.attribute(first.id, "t-two")
    attribution.attribute(second.id, "t-two")

    attribution.unattribute(first.id, "t-two")

    assert [r.feature_id
            for r in attribution.features_for_session("t-two")] == [second.id]


def test_unattribute_resolves_the_canonical_trace(session_row, run_alias,
                                                  feature_row):
    """A run trace and the conversation it drove are one session, so undoing
    by either id has to reach the one row `attribute` wrote."""
    feature = feature_row()
    session_row("t-conversation")
    run_alias("t-run", "t-conversation")
    attribution.attribute(feature.id, "t-run")

    assert attribution.unattribute(feature.id, "t-run") is True
    assert attribution.features_for_session("t-conversation") == []


def test_unattribute_cannot_remove_a_run_lane_attribution(
        session_row, task_lane, feature_row):
    """There is no row to delete: the task's placement under the feature is
    the assertion, and withdrawing it is an act on `/tasks/:id`, not here."""
    feature = feature_row()
    session_row("t-lane-undo")
    task_lane(810, feature.id, [("t-lane-undo", "done")])

    assert attribution.unattribute(feature.id, "t-lane-undo") is False
    assert [r.feature_id for r in attribution.attributions_for_session(
        "t-lane-undo")] == [feature.id]


@pytest.mark.parametrize("feature_id,trace", [(None, "t-x"), (1, "")])
def test_unattribute_refuses_an_empty_argument(feature_id, trace):
    with pytest.raises(ValueError):
        attribution.unattribute(feature_id, trace)


def test_unattribute_leaves_a_span_of_its_own(session_row, posted_spans,
                                              feature_row):
    """`session_spans` is append-only, so the withdrawal is a second span
    rather than the erasure of the first."""
    from lib.features import spans
    feature = feature_row()
    session_row("t-span")
    attribution.attribute(feature.id, "t-span")
    attribution.unattribute(feature.id, "t-span")

    spans.unattributed(feature.id, "t-span")

    assert [(p["name"], p["attributes"]["feature_id"]) for p in posted_spans
            if p["name"] == "feature.unattributed"] == [
        ("feature.unattributed", feature.id)]


def test_the_withdrawal_span_hangs_on_the_attributed_trace(
        session_row, run_alias, posted_spans):
    """The pair reads as one session's history, not as an event in whichever
    session happened to press the button."""
    from lib.features import spans
    session_row("t-conv")
    run_alias("t-run2", "t-conv")

    spans.unattributed(917, attribution.canonical_trace("t-run2"))

    assert posted_spans[-1]["trace_id"] == "t-conv"
