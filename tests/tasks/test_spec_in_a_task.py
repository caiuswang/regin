"""Where a feature's spec lives, and what bounds it (CAI-272).

The spec for a feature now lives in a task's `body`, so the edit path's bound
has to clear the size a real spec reaches — 29,488 characters is the largest
measured artifact, which is a 92% fill of 32,000. Both body bounds **refuse**
rather than truncating; only the `issue_cache` clamp still clips, and its bound
moves with them.

A top-level task mints its key on creation and reads stage `spec` before any
session exists — which is how a feature is declared before it is built, and
therefore before anything names it as parent.
"""

from __future__ import annotations

import pytest

from lib.orm.models.tasks import BODY_MAX
from lib.tasks import capture, store
from lib.tasks.integrations.base import DESCRIPTION_MAX, clamp_description
from lib.tasks.integrations.regin import key_for_task


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


def test_neither_body_bound_truncates():
    """A spec silently clipped mid-sentence is worse than one refused."""
    with pytest.raises(capture.CaptureRejected):
        capture.parse_capture({"title": "t", "source": "test",
                               "body": "x" * 8001})
    with pytest.raises(capture.CaptureRejected):
        capture.parse_update({"body": "x" * 64_001})


def test_clamp_description_does_not_truncate_under_sixty_four_thousand():
    """Two live `issue_cache` rows were clipped to exactly 8,000."""
    assert DESCRIPTION_MAX == 64_000
    body = "x" * 29_488
    assert clamp_description(body) == body


def test_a_top_level_spec_task_mints_its_key_on_creation(native_task):
    task_id = native_task(title="a spec")
    key = key_for_task(task_id)
    from lib.tasks.integrations import registry

    provider, ref = registry.resolve_ref(key)
    assert provider.fetch_issue(ref).key == key


def test_a_spec_task_reads_stage_spec_before_any_session_exists(native_task):
    from lib.defects import stage as stage_module

    task_id = native_task(title="a spec")
    result = stage_module.stage_for_key(key_for_task(task_id))
    assert result.name == "spec"
    assert result.rounds == 1 and result.open_defects == 0


def test_feature_stage_resolves_a_spec_task_that_is_in_no_population_arm(
        native_task):
    """Declared and invisible in the index — stated as intent, not a bug."""
    from typer.testing import CliRunner
    from cli.commands.feature import feature_app

    task_id = native_task(title="a spec")
    result = CliRunner().invoke(feature_app, ["stage", key_for_task(task_id)])
    assert result.exit_code == 0, result.output
    assert "spec" in result.output


def test_a_sixty_four_thousand_character_body_round_trips_through_the_store(
        native_task):
    """The end-to-end claim: a spec this size survives being written and read
    back, and the cache row it produces is not clipped."""
    from lib.tasks import issue_cache

    task_id = native_task(title="a spec")
    body = "y" * 40_000
    store.update_task(task_id, {"body": body})
    assert store.get_task(task_id)["body"] == body

    snapshot, _ = issue_cache.lookup("regin", key_for_task(task_id))
    assert snapshot is not None and len(snapshot["description"]) == 40_000
