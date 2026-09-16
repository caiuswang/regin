"""Withdrawing a declaration (CAI-244).

`attribute` was upsert-only and there was no verb to undo it. Un-declaring has
to exist before a browser can offer it, and it leaves a span of its own
because `session_spans` is append-only — the declaration is not erased from
history, it is countered.
"""

from __future__ import annotations

import pytest

from lib.features import attribution


def test_unattribute_deletes_the_row(session_row):
    session_row("t-undo")
    attribution.attribute("CAI-910", "t-undo")

    assert attribution.unattribute("CAI-910", "t-undo") is True
    assert attribution.features_for_session("t-undo") == []


def test_unattribute_of_an_absent_row_is_false(session_row):
    session_row("t-absent")

    assert attribution.unattribute("CAI-910", "t-absent") is False


def test_unattribute_leaves_other_features_of_the_session_alone(session_row):
    session_row("t-two")
    attribution.attribute("CAI-911", "t-two")
    attribution.attribute("CAI-912", "t-two")

    attribution.unattribute("CAI-911", "t-two")

    assert [r.feature_key
            for r in attribution.features_for_session("t-two")] == ["CAI-912"]


def test_unattribute_resolves_the_canonical_trace(session_row, run_alias):
    """A run trace and the conversation it drove are one session, so undoing
    by either id has to reach the one row `attribute` wrote."""
    session_row("t-conversation")
    run_alias("t-run", "t-conversation")
    attribution.attribute("CAI-913", "t-run")

    assert attribution.unattribute("CAI-913", "t-run") is True
    assert attribution.features_for_session("t-conversation") == []


def test_unattribute_cannot_remove_a_task_lane_attribution(
        session_row, task_lane):
    """There is no row to delete: the link on the task is the assertion, and
    withdrawing it is an act on `/tasks/:id`, not here."""
    session_row("t-lane-undo")
    task_lane(810, ["CAI-914"], [("t-lane-undo", "done")])

    assert attribution.unattribute("CAI-914", "t-lane-undo") is False
    assert [r.feature_key for r in
            attribution.attributions_for_session("t-lane-undo")] == ["CAI-914"]


@pytest.mark.parametrize("key,trace", [("", "t-x"), ("CAI-915", "")])
def test_unattribute_refuses_an_empty_argument(key, trace):
    with pytest.raises(ValueError):
        attribution.unattribute(key, trace)


def test_unattribute_leaves_a_span_of_its_own(session_row, posted_spans):
    """`session_spans` is append-only, so the withdrawal is a second span
    rather than the erasure of the first."""
    from lib.features import spans
    session_row("t-span")
    attribution.attribute("CAI-916", "t-span")
    attribution.unattribute("CAI-916", "t-span")

    spans.unattributed("CAI-916", "t-span")

    assert [(p["name"], p["attributes"]["feature_key"]) for p in posted_spans
            if p["name"] == "feature.unattributed"] == [
        ("feature.unattributed", "CAI-916")]


def test_the_withdrawal_span_hangs_on_the_attributed_trace(
        session_row, run_alias, posted_spans):
    """The pair reads as one session's history, not as an event in whichever
    session happened to press the button."""
    from lib.features import spans
    session_row("t-conv")
    run_alias("t-run2", "t-conv")

    spans.unattributed("CAI-917", attribution.canonical_trace("t-run2"))

    assert posted_spans[-1]["trace_id"] == "t-conv"
