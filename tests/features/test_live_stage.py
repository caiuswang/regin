"""A live stage — a tracker whose cache cannot lag the fact (CAI-269).

The native provider's issues live in the same database as the cache, so there
is nothing to refresh: the surface says `live` where it would otherwise say
`as of T`, and offers no Refresh control. `stage_is_live` is a **declared
capability**, read through a registry lookup, so no caller outside a provider
module branches on `provider_id` — which is the property that makes a second
local tracker an additive change.

The refresh *endpoint* stays uniform. Only the control disappears: gating or
409-ing the route would be a provider branch in transport, and `features.py`'s
editor gate survives on its second reason anyway (it writes a shared cache).
"""

from __future__ import annotations

import pytest

from lib.tasks.integrations import registry
from lib.tasks.integrations.regin import key_for_task


def test_the_native_provider_declares_stage_is_live():
    assert registry.build_provider("regin").stage_is_live is True


def test_a_provider_that_does_not_declare_it_is_not_live():
    assert registry.build_provider("linear").stage_is_live is False


def test_stage_live_is_a_registry_lookup_not_a_provider_branch():
    """A caller asks what the provider declares; it never names a vendor."""
    import inspect

    source = inspect.getsource(registry.stage_is_live)
    assert "linear" not in source and "regin" not in source
    assert registry.stage_is_live("regin") is True
    assert registry.stage_is_live("linear") is False
    assert registry.stage_is_live("nothing-registered") is False


def test_a_live_features_payload_carries_stage_live_true(flask_client,
                                                         native_task):
    task_id = native_task(title="native feature")
    body = flask_client.get(f"/api/features/{key_for_task(task_id)}").get_json()
    assert body["stage_live"] is True


def test_a_linear_features_payload_is_unchanged(flask_client, cached_issue):
    cached_issue("CAI-1", provider="linear")
    body = flask_client.get("/api/features/CAI-1").get_json()
    assert body["stage_live"] is False
    assert body["stage_synced_at"] == "2026-09-01 12:00:00"


def test_editing_a_parents_title_updates_the_header_without_a_child_event(
        native_task):
    """The fifth cache trigger. A rename changes what the header says with no
    run involved, so a child-event-only trigger would never fire for it."""
    from lib.tasks import issue_cache, store

    task_id = native_task(title="before")
    store.update_task(task_id, {"title": "after"})
    snapshot, _ = issue_cache.lookup("regin", key_for_task(task_id))
    assert snapshot["title"] == "after"


def test_the_parent_edit_cache_write_happens_in_the_store_not_the_route(
        native_task):
    """All eight existing `remember` call sites are fetch paths, and `regin
    task ship` bypasses HTTP entirely — a route-side trigger would not fire."""
    import inspect

    from lib.tasks import store
    import web.blueprints.tasks as route_module

    assert "_remember_if_a_feature" in inspect.getsource(store.update_task)
    assert "remember_task" not in inspect.getsource(route_module)


def test_the_refresh_endpoint_is_uniform_across_providers(flask_client,
                                                          native_task):
    """Not gated, not 409'd, not branched — only the *control* disappears."""
    task_id = native_task(title="native feature")
    resp = flask_client.post(
        f"/api/features/{key_for_task(task_id)}/stage/refresh")
    assert resp.status_code == 200
    assert resp.get_json()["stage"]["stage"] == "spec"


def test_stage_live_is_absent_from_the_index_payload(flask_client,
                                                     native_task):
    """`FeaturesView.vue` renders no freshness and no Refresh at all, and the
    key shape already tells the two apart."""
    from lib.tasks import grain

    parent = native_task()
    grain.set_parent(native_task(), parent)
    page = flask_client.get("/api/features").get_json()
    assert page["items"]
    assert all("stage_live" not in row for row in page["items"])


def test_a_relative_link_url_is_what_the_native_provider_returns():
    """The SPA keys the router-link on the url's SHAPE, so the renderer needs
    no provider field — see `frontend/tests/native-live-stage.spec.js`."""
    _, ref = registry.resolve_ref("regin:42")
    assert ref.url == "/tasks/42"
    assert not ref.url.startswith("http")


def test_links_serialize_gains_stage_live(native_task, monkeypatch):
    from lib.orm import SessionLocal
    from lib.orm.models.tasks import AgentTaskLink
    from lib.tasks import links

    task_id = native_task()
    with SessionLocal() as session:
        row = AgentTaskLink(task_id=task_id, provider="linear",
                            external_key="CAI-7", url="https://x/CAI-7",
                            created_at="2026-09-01 10:00:00")
        session.add(row)
        session.commit()
        session.refresh(row)
        view = links.serialize(row)
    assert view["stage_live"] is False
