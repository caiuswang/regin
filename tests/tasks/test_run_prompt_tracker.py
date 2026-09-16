"""The `<tracker>` block a run of a remotely-tracked feature launches with.

The claim under test is that a remote reference is carried *forward* into the
work, not just pulled once at capture time: a run whose feature names a tracker
issue opens with that issue named, so the agent knows there is a tracker to
read and to record against.

**It hangs off the FEATURE, which is what this redesign moved.** The link used
to live on a task, so which issue a run belonged to depended on which of a
feature's tasks launched it — and a feature's two tasks could name two
different issues. A feature carries one `external_ref`, so every run of it
opens with the same one.

A native feature gains nothing: the block is absent, not empty. Nothing here
reaches a tracker — what is being pinned is the prompt's shape.
"""

from __future__ import annotations

import pytest

from lib.features import store as feature_store
from lib.tasks import runs


@pytest.fixture
def remote_feature():
    def _make(ref="CAI-1", title="the feature"):
        return feature_store.create(title, origin="linear", external_ref=ref,
                                    external_url=f"https://x/{ref}")
    return _make


def _block(feature) -> str:
    return runs._tracker_block(feature)


def test_a_remote_feature_names_its_issue_and_its_url(remote_feature):
    block = _block(remote_feature("CAI-42"))

    assert "<tracker>" in block and "</tracker>" in block
    assert "CAI-42" in block
    assert "https://x/CAI-42" in block


def test_the_block_names_the_provider_through_the_registry(remote_feature):
    """Never by knowing what Linear is called here — the rule that keeps a
    second tracker additive."""
    from lib.tasks.integrations import registry

    assert registry.display_name("linear") in _block(remote_feature())


def test_a_native_feature_gains_no_block_at_all(feature_row):
    """Absent, not empty: an empty block is something the agent has to read
    and resolve, and an unlinked run's prompt is exactly what it was."""
    assert _block(feature_row()) == ""


def test_a_task_under_no_feature_gains_no_block(feature_row):
    assert _block(None) == ""


def test_the_block_names_the_issue_without_repeating_its_description(
        remote_feature):
    """Identity, not content. The feature already carries the spec, and a
    second copy is either duplication or a stale contradiction of one."""
    feature = feature_store.create("the feature", body="the whole spec here",
                                   origin="linear", external_ref="CAI-7")

    block = _block(feature)

    assert "CAI-7" in block
    assert "the whole spec here" not in block


def test_the_block_tells_the_agent_to_record_before_reporting_finished(
        remote_feature):
    """regin re-reads the issue when the finish command lands, so a state
    changed afterwards is not the one the feature will show."""
    block = _block(remote_feature())

    assert "before you run the finish command" in block.lower()


def test_a_remote_features_run_is_told_to_move_the_issue_first(
        remote_feature, native_task):
    """The `<finish>` block gains the ordering only for a run that has an
    issue to move; an unlinked run is told nothing about a tracker, because
    there is none and the instruction would be noise it has to resolve."""
    linked = runs._finish_block(1, linked=True)
    unlinked = runs._finish_block(1, linked=False)

    assert "record the work on the linked issue" in linked
    assert "linked issue" not in unlinked
