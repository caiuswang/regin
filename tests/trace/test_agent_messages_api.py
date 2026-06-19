"""Endpoint tests for the send_to_user inbox API.

Uses the authenticated `flask_client` (every /api/ route is JWT-gated).
Rows are seeded straight through the store, then read/mutated via HTTP.
"""

from __future__ import annotations

from lib.agent_messages import store


def _seed(trace_id="sess-a", body="hi", **kw):
    return store.record_message(trace_id=trace_id, body=body,
                                dispatch_webhook=False, **kw)


def test_inbox_endpoint_returns_messages_and_unread(flask_client):
    _seed(body="one")
    _seed(body="two", msg_type="warning")
    resp = flask_client.get("/api/agent-messages/inbox")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["messages"]) == 2
    assert data["unread_count"] == 2


def test_unread_count_endpoint(flask_client):
    _seed()
    resp = flask_client.get("/api/agent-messages/unread-count")
    assert resp.get_json()["count"] == 1


def test_mark_read_endpoint_clears_unread(flask_client):
    m = _seed()
    resp = flask_client.post("/api/agent-messages/read", json={"ids": [m["id"]]})
    assert resp.get_json()["marked"] == 1
    assert flask_client.get(
        "/api/agent-messages/unread-count").get_json()["count"] == 0


def test_dismiss_endpoint_removes_from_inbox(flask_client):
    m = _seed()
    flask_client.post(f"/api/agent-messages/{m['id']}/dismiss")
    assert flask_client.get(
        "/api/agent-messages/inbox").get_json()["messages"] == []


def test_session_feed_endpoint(flask_client):
    _seed(trace_id="sess-x", body="step 1")
    _seed(trace_id="sess-x", body="step 2")
    resp = flask_client.get("/api/sessions/sess-x/agent-messages")
    msgs = resp.get_json()["messages"]
    assert [m["body"] for m in msgs] == ["step 1", "step 2"]


def test_inbox_requires_auth(anon_client):
    assert anon_client.get("/api/agent-messages/inbox").status_code == 401
