"""Where a feature's spec lives, and what bounds it.

The spec lives on `features.body` now — it used to live in a task's `body`,
which is the overload this redesign removed. The bounds are unchanged and both
still **refuse** rather than truncating: a spec silently clipped mid-sentence
is worse than one the operator is told did not fit. 29,488 characters is the
largest measured artifact, a 92% fill of 32,000.

A feature reads stage `spec` from the moment it is created, before any session
exists — which is how one is declared before it is built.
"""

from __future__ import annotations

import pytest

from lib.features import events, store as feature_store
from lib.orm.models.tasks import BODY_MAX
from lib.tasks import capture
from lib.tasks.integrations.base import DESCRIPTION_MAX, clamp_description


def test_capture_refuses_a_body_over_the_capture_bound():
    """Third-party intake keeps 8,000: a webhook is not writing a spec."""
    assert capture.CAPTURE_BODY_MAX == 8000
    with pytest.raises(capture.CaptureRejected) as caught:
        capture.parse_capture({"title": "t", "source": "test",
                               "body": "x" * 8001})
    assert caught.value.field == "body"


def test_the_edit_path_accepts_a_body_up_to_sixty_four_thousand():
    assert BODY_MAX == 64_000
    fields = capture.parse_update({"body": "x" * 64_000})
    assert len(fields["body"]) == 64_000


def test_a_feature_spec_is_bounded_and_refuses_rather_than_truncating():
    assert feature_store.create("t", body="x" * 64_000).body
    with pytest.raises(feature_store.FeatureError):
        feature_store.create("t2", body="x" * 64_001)


def test_neither_body_bound_truncates():
    """A spec silently clipped mid-sentence is worse than one refused."""
    with pytest.raises(capture.CaptureRejected):
        capture.parse_capture({"title": "t", "source": "test",
                               "body": "x" * 8001})
    with pytest.raises(capture.CaptureRejected):
        capture.parse_update({"body": "x" * 64_001})


def test_clamp_description_does_not_truncate_under_sixty_four_thousand():
    """Two live cache rows were clipped to exactly 8,000."""
    assert DESCRIPTION_MAX == 64_000
    body = "x" * 29_488
    assert clamp_description(body) == body


def test_a_feature_reads_stage_spec_before_any_session_exists():
    feature = feature_store.create("a spec")

    assert events.stage(feature.id) == events.STAGE_SPEC
    assert events.rounds(feature.id) == 1
    assert events.last(feature.id).kind == events.SPECCED
