"""Acceptance items for CAI-214 — rounds carried on the fetched issue.

The adapter is exercised with its transport stubbed: the payloads below are the
shape Linear's schema declares (`IssueStateSpan: id, stateId, startedAt,
endedAt, state`), confirmed by introspection rather than assumed.
"""

from __future__ import annotations

import pytest

from lib.tasks.integrations.base import ExternalRef
from lib.tasks.integrations.linear import LinearProvider, _QUERY


def _issue(*spans) -> dict:
    return {
        "identifier": "CAI-206", "title": "Defect records", "url": "https://x/",
        "state": {"name": "Done", "type": "completed"},
        "stateHistory": {"nodes": list(spans)},
    }


def _span(state_type: str, ended):
    return {"startedAt": "2026-09-13T01:00:00.000Z", "endedAt": ended,
            "state": {"name": state_type.title(), "type": state_type}}


@pytest.fixture
def provider(monkeypatch):
    prov = LinearProvider()
    monkeypatch.setattr(prov, "is_configured", lambda: True)
    return prov


def _fetch(provider, monkeypatch, node):
    monkeypatch.setattr(provider, "_issue_node", lambda key: node)
    return provider.fetch_issue(ExternalRef(provider="linear", key="CAI-206",
                                            url="https://x/"))


def test_the_query_requests_state_history():
    assert "stateHistory" in _QUERY
    assert "endedAt" in _QUERY


def test_rounds_ride_the_mapped_issue(provider, monkeypatch):
    issue = _fetch(provider, monkeypatch, _issue(_span("completed", None)))

    assert issue.rounds == 1


def test_an_issue_with_no_history_maps_to_one_round(provider, monkeypatch):
    node = _issue()
    node.pop("stateHistory")

    assert _fetch(provider, monkeypatch, node).rounds == 1


def test_a_reopened_history_maps_to_more_than_one_round(provider, monkeypatch):
    issue = _fetch(provider, monkeypatch, _issue(
        _span("started", "t1"),
        _span("completed", "t2"),      # walked back
        _span("started", "t3"),
        _span("completed", None)))

    assert issue.rounds == 2


def test_rounds_survive_the_snapshot_round_trip(provider, monkeypatch):
    """The cached snapshot is what every consumer reads, so a count that does
    not serialise is a count nobody sees."""
    issue = _fetch(provider, monkeypatch, _issue(
        _span("completed", "t1"), _span("started", None)))

    assert issue.to_dict()["rounds"] == 2


def test_an_unconfigured_tracker_reports_that_rather_than_a_number(monkeypatch):
    """`1` is what a real never-reopened feature reads, so it must never
    double as "we could not look" — the caller could not tell the floor from
    the truth. Forced off here rather than skipped, so the assertion runs on a
    machine that *does* have a key."""
    from lib.defects import tracker  # noqa: PLC0415
    from lib.settings import settings  # noqa: PLC0415

    monkeypatch.setattr(settings.task_integrations, "linear_enabled", False)
    monkeypatch.setattr(settings.task_integrations, "linear_api_key", None)

    with pytest.raises(tracker.TrackerUnavailable):
        tracker.rounds_for("CAI-206")
