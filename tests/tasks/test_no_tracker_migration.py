"""A feature never moves between trackers (CAI-271).

`feature_key` is identity and is never rewritten — no verb, no button, no
alias, no second link, in either direction. A rewrite would lie about
provenance three unrepairable ways: write-once `defects.blamed_against_state`
re-attributed to a task that never held that state, branch names rooted on the
old key in unrewritable git history, and an orphaned `issue_cache` row.

The refusals are at the **act**, not at read. All three doors: attaching a link
to a task that already holds one, attaching a native ref at all, and setting a
child's `parent_id` onto a linked task.
"""

from __future__ import annotations

import pytest

from lib.tasks import grain, links, store
from lib.tasks.integrations import IntegrationError, registry


@pytest.fixture
def linear_pinned(monkeypatch):
    """A Linear that answers, without a network call."""
    from lib.tasks.integrations.base import (
        ExternalIssue, ExternalRef, IssueTrackerProvider,
    )

    class _Linear(IssueTrackerProvider):
        provider_id = "linear"
        display_name = "Linear"
        ref_hint = "an identifier like ENG-42"

        def is_configured(self):
            return True

        def parse_ref(self, text):
            raw = (text or "").strip()
            if not raw.startswith("CAI-"):
                return None
            return ExternalRef(provider="linear", key=raw,
                               url=f"https://linear.app/issue/{raw}")

        def fetch_issue(self, ref):
            return ExternalIssue(provider="linear", key=ref.key, url=ref.url,
                                 title="a linear issue",
                                 state_category="started")

    monkeypatch.setitem(registry._PROVIDER_BUILDERS, "linear", _Linear)
    return _Linear


def test_there_is_no_verb_to_move_a_feature_between_trackers():
    """Not a missing button — a missing verb, in every module that writes."""
    import inspect

    from lib.features import attribution
    from lib.tasks import links as links_module

    for module in (store, links_module, attribution, grain):
        names = {name for name in dir(module) if not name.startswith("_")}
        assert not names & {"migrate", "migrate_feature", "move_feature",
                            "rekey", "retarget", "relink", "alias_key"}, module


def test_attach_refuses_on_a_task_that_already_holds_a_link(
        native_task, linear_pinned):
    task_id = native_task()
    links.attach(task_id, "CAI-1")
    with pytest.raises(IntegrationError) as caught:
        links.attach(task_id, "CAI-2")
    assert "two feature keys" in str(caught.value)


def test_attach_refuses_a_native_ref_outright(native_task):
    """A regin task IS a feature; linking one to another would be the move."""
    task_id = native_task()
    other = native_task()
    with pytest.raises(IntegrationError) as caught:
        links.attach(task_id, f"regin:{other}")
    assert "regin" in str(caught.value).lower()


def test_attach_refuses_on_a_task_that_already_has_children(
        native_task, linear_pinned):
    parent = native_task()
    grain.set_parent(native_task(), parent)
    with pytest.raises(IntegrationError) as caught:
        links.attach(parent, "CAI-3")
    assert "two feature keys" in str(caught.value)


def test_setting_a_parent_id_refuses_when_that_parent_holds_a_link(
        native_task, linear_pinned):
    holder = native_task()
    links.attach(holder, "CAI-4")
    with pytest.raises(grain.GrainRejected) as caught:
        grain.set_parent(native_task(), holder)
    assert "two feature keys" in str(caught.value)


def test_preview_refuses_exactly_as_attach_does(native_task):
    """The composer must not offer a shape the attach behind it will refuse."""
    with pytest.raises(IntegrationError):
        links.preview(f"regin:{native_task()}")


def test_a_task_grain_error_is_an_integration_error_and_maps_to_400(
        flask_client, native_task, linear_pinned):
    task_id = native_task()
    flask_client.post(f"/api/tasks/{task_id}/links", json={"ref": "CAI-5"})
    second = flask_client.post(f"/api/tasks/{task_id}/links",
                               json={"ref": "CAI-6"})
    assert second.status_code == 400
    assert "two feature keys" in second.get_json()["error"]


def test_the_registry_still_claims_a_native_ref_for_features_and_fix_item():
    """The refusal is on *attaching*, not on resolving: `regin feature ledger
    regin:42` and `regin defects fix-item --issue regin:42` both still work."""
    provider, ref = registry.resolve_ref("regin:42")
    assert provider.provider_id == "regin" and ref.key == "regin:42"
    assert registry.name_ref("regin:42").key == "regin:42"


def test_the_extracted_lane_carries_the_ambiguity_guard():
    from lib.features import lane

    assert "ambiguous" in lane.LANE and "ambiguous_keys" in lane.LANE
