"""Endpoint test for the event-bus catalog API (`/api/events/kinds`).

Uses the authenticated `flask_client`; the endpoint returns the same
registry `regin events list` reads.
"""

from __future__ import annotations

from lib.agent_messages import events


def test_events_kinds_endpoint_lists_registry(flask_client):
    resp = flask_client.get("/api/events/kinds")
    assert resp.status_code == 200
    kinds = resp.get_json()["kinds"]
    assert len(kinds) == len(events.REGISTRY)
    by_kind = {k["kind"]: k for k in kinds}
    assert "proposal.ready" in by_kind
    assert "content.drift" in by_kind
    assert "grade.finished" in by_kind
    row = by_kind["proposal.ready"]
    assert set(row) == {"kind", "severity", "default_enabled",
                        "enabled", "summary"}
