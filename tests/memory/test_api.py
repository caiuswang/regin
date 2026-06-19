"""/api/memory/* endpoints through the authenticated Flask client."""

from __future__ import annotations

import lib.memory as memory


def _seed():
    return memory.remember("Restart backend before Playwright runs.",
                           kind="gotcha", title="Stale backend",
                           is_test=True)


def test_list_and_stats(flask_client):
    _seed()
    data = flask_client.get(
        "/api/memory?include_tests=true").get_json()
    assert len(data["items"]) == 1
    assert data["pagination"]["total"] == 1
    assert data["stats"]["total"] == 1


def test_get_patch_and_forget(flask_client):
    mid = _seed()
    assert flask_client.get(f"/api/memory/{mid}").get_json()[
        "memory"]["title"] == "Stale backend"

    r = flask_client.patch(f"/api/memory/{mid}",
                           json={"title": "Renamed", "recall_count": 7})
    assert r.status_code == 200  # non-editable keys are dropped, not fatal
    assert memory.get_store().get_dict(mid)["title"] == "Renamed"

    assert flask_client.delete(f"/api/memory/{mid}").status_code == 200
    assert flask_client.get(f"/api/memory/{mid}").status_code == 404


def test_approve_and_retire(flask_client):
    mid = memory.remember("proposed entry", status="proposed", is_test=True)
    assert flask_client.post(
        f"/api/memory/{mid}/approve").status_code == 200
    assert memory.get_store().get_dict(mid)["status"] == "active"

    assert flask_client.post(f"/api/memory/{mid}/retire",
                             json={"wrong": True}).status_code == 200
    row = memory.get_store().get_dict(mid)
    assert row["status"] == "retired" and row["veracity"] == "false"


def test_recall_probe(flask_client):
    _seed()
    data = flask_client.post("/api/memory/recall", json={
        "query": "playwright stale backend", "mode": "fts",
        "include_tests": True}).get_json()
    assert data["hits"] and data["hits"][0]["title"] == "Stale backend"
    assert flask_client.post("/api/memory/recall",
                             json={}).status_code == 400


def test_reflect_endpoint(flask_client):
    _seed()
    data = flask_client.post("/api/memory/reflect",
                             json={"dry_run": True}).get_json()
    assert data["dry_run"] is True and data["examined"] == 1


def test_memory_routes_require_auth(anon_client):
    assert anon_client.get("/api/memory").status_code == 401


# ── exemplar curation (build-a-case) endpoints ─────────────────────────

def test_exemplar_add_validates(flask_client):
    mid = _seed()
    # blank query / bad polarity are rejected.
    assert flask_client.post("/api/memory/exemplars", json={
        "memory_id": mid, "polarity": "positive"}).status_code == 400
    assert flask_client.post("/api/memory/exemplars", json={
        "memory_id": mid, "query": "x", "polarity": "sideways"}
    ).status_code == 400
    # neither memory_id nor topic_id → 400.
    assert flask_client.post("/api/memory/exemplars", json={
        "query": "x", "polarity": "positive"}).status_code == 400


def test_exemplar_add_remove_roundtrip(flask_client, monkeypatch):
    """A manual positive case is written via POST and dropped via DELETE.
    Uses a stub embedder so the write doesn't need the real model."""
    from tests.memory.test_store import _StubEmbedder
    monkeypatch.setattr(memory.get_store(), "_embedder", _StubEmbedder({}))
    mid = _seed()

    r = flask_client.post("/api/memory/exemplars", json={
        "memory_id": mid, "query": "how to restart the backend",
        "polarity": "positive"})
    assert r.status_code == 200 and r.get_json()["written"] == 1

    summary = flask_client.get("/api/memory/exemplars").get_json()["summary"]
    row = next(s for s in summary if s["memory_id"] == mid)
    assert row["pos_count"] == 1 and row["neg_count"] == 0

    r = flask_client.delete("/api/memory/exemplars", json={
        "memory_id": mid, "polarity": "positive"})
    assert r.status_code == 200 and r.get_json()["removed"] == 1
    assert flask_client.get("/api/memory/exemplars").get_json()["summary"] == []


# ── Loopback exemption for the auto-inject hook's dense recall ──────────

def test_recall_loopback_exempt_when_server_path_enabled(anon_client, monkeypatch):
    """A fresh hook process can't carry a JWT. With the server-dense inject
    path on, POST /api/memory/recall from loopback is allowed without auth —
    that's the warm-model borrow the inject hook depends on."""
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "inject_dense_via_server", True)
    _seed()
    r = anon_client.post("/api/memory/recall", json={
        "query": "playwright stale backend", "mode": "fts",
        "include_tests": True})
    assert r.status_code == 200
    assert r.get_json()["hits"][0]["title"] == "Stale backend"


def test_recall_requires_auth_when_server_path_disabled(anon_client, monkeypatch):
    """Flag off → no exemption → the endpoint is gated like any other."""
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "inject_dense_via_server", False)
    assert anon_client.post("/api/memory/recall", json={
        "query": "x", "include_tests": True}).status_code == 401


def test_loopback_exemption_is_scoped_to_recall(anon_client, monkeypatch):
    """The exemption covers ONLY /api/memory/recall — other memory routes
    stay gated even from loopback, so memory content isn't broadly exposed."""
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "inject_dense_via_server", True)
    assert anon_client.get("/api/memory").status_code == 401
