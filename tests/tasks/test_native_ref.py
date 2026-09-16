"""The native ref — `regin:<task id>` (CAI-261).

The claim sets of the two providers have to be disjoint *by construction*, not
merely by luck: a colon defeats Linear's `fullmatch`, and a bare number is
claimed by neither. That is what makes `AmbiguousRef` between them unreachable
rather than unlikely, so no caller ever has to disambiguate.
"""

from __future__ import annotations

import pytest

from lib.tasks.integrations import registry
from lib.tasks.integrations.linear import LinearProvider
from lib.tasks.integrations.regin import ReginProvider, key_for_task


@pytest.fixture
def native():
    return ReginProvider()


def test_a_native_ref_is_regin_colon_task_id_and_is_slash_free(native):
    ref = native.parse_ref("regin:42")
    assert ref is not None
    assert ref.provider == "regin"
    assert ref.key == "regin:42"
    assert "/" not in ref.key
    assert key_for_task(42) == "regin:42"


def test_linear_does_not_claim_a_native_ref():
    assert LinearProvider().parse_ref("regin:42") is None


def test_native_does_not_claim_a_bare_number(native):
    assert native.parse_ref("42") is None


def test_a_pasted_tasks_url_is_claimed_whatever_the_host(native):
    for url in ("http://localhost:8321/tasks/42",
                "https://regin.example.com/tasks/42",
                "https://some-other-box.internal:9000/tasks/42"):
        ref = native.parse_ref(url)
        assert ref is not None, url
        assert ref.key == "regin:42", url


def test_regin_042_canonicalizes_to_regin_42(native):
    for text in ("Regin:42", "regin:042", "REGIN:0042",
                 "http://x/tasks/042", "  regin:42  "):
        ref = native.parse_ref(text)
        assert ref is not None, text
        assert ref.key == "regin:42", text


def test_regin_0_and_a_signed_number_are_not_claimed(native):
    for text in ("regin:0", "regin:000", "regin:-1", "regin:+1",
                 "http://x/tasks/0", "regin:", "regin:abc"):
        assert native.parse_ref(text) is None, text


def test_the_url_of_a_native_issue_is_the_relative_tasks_path(native):
    assert native.parse_ref("regin:42").url == "/tasks/42"
    # Even a pasted absolute URL canonicalizes to the relative one: regin has
    # no authoritative self-address to rebuild an absolute one from.
    assert native.parse_ref("https://elsewhere/tasks/42").url == "/tasks/42"


def test_a_native_key_and_a_linear_key_can_never_collide(native):
    linear = LinearProvider()
    for text in ("regin:42", "regin:1", "/tasks/7", "http://x/tasks/7"):
        assert linear.parse_ref(text) is None, text
    for text in ("CAI-274", "ENG-42",
                 "https://linear.app/caiuswang/issue/CAI-274"):
        assert native.parse_ref(text) is None, text


def test_the_features_route_resolves_a_native_key_unencoded_and_encoded(
        flask_client, native_task):
    task_id = native_task(title="a feature")
    key = f"regin:{task_id}"
    plain = flask_client.get(f"/api/features/{key}")
    encoded = flask_client.get(f"/api/features/regin%3A{task_id}")
    assert plain.status_code == 200
    assert encoded.status_code == 200
    assert plain.get_json()["key"] == key
    assert encoded.get_json()["key"] == key


def test_the_registry_claims_a_native_ref_without_any_credential(monkeypatch):
    """`regin:42` resolves with Linear pinned off — the point of the feature.

    Linear is pinned off here rather than through `no_tracker`, which means
    *nothing answers* and therefore drops the native builder too.
    """
    from lib.settings import settings

    monkeypatch.setattr(settings.task_integrations, "linear_enabled", False)
    monkeypatch.setattr(settings.task_integrations, "linear_api_key", None)
    provider, ref = registry.resolve_ref("regin:42")
    assert provider.provider_id == "regin"
    assert ref.key == "regin:42"
