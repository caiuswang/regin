"""The snapshot refresh a finished run earns.

A linked run is launched with instructions to move the issue's state to match
the work, so the moment it reports itself finished is the moment regin's cached
snapshot is known stale. The claim under test is that the report itself
refreshes it: the next person to open the task reads the issue as it now
stands, without having to click Refresh on the card to discover what the agent
already did.

The second claim is that the refresh cannot cost anything. A tracker being
unreachable must not unsettle the attempt or hold back a dependent, a replayed
finish must not spend a second round trip, and an unlinked task must reach no
provider at all.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlmodel import select

from lib.orm import SessionLocal
from tests.issue_cache_seed import cached, seed
from lib.orm.models.tasks import AgentTaskLink, AgentTaskRun
from lib.tasks import runs
from lib.tasks.integrations import registry
from lib.tasks.integrations.base import (
    ExternalIssue, ExternalRef, IntegrationError, IssueTrackerProvider,
    STATE_COMPLETED, STATE_UNSTARTED,
)


class FakeTracker(IssueTrackerProvider):
    """A tracker whose issue has moved on since the link was attached."""

    provider_id = "fake"
    display_name = "Fake Tracker"
    ref_hint = "a fake:// reference"
    error: Exception | None = None
    calls = 0

    def is_configured(self) -> bool:
        return True

    def parse_ref(self, text: str):
        return None

    def fetch_issue(self, ref: ExternalRef) -> ExternalIssue:
        type(self).calls += 1
        if type(self).error is not None:
            raise type(self).error
        return ExternalIssue(
            provider=self.provider_id, key=ref.key, url=ref.url,
            title=f"{ref.key} as the tracker titles it", state="Done",
            state_category=STATE_COMPLETED)


@pytest.fixture
def fake_tracker(monkeypatch):
    monkeypatch.setitem(registry._PROVIDER_BUILDERS, "fake", FakeTracker)
    FakeTracker.error = None
    FakeTracker.calls = 0
    yield FakeTracker
    FakeTracker.error = None


@pytest.fixture
def launcher(monkeypatch):
    """`launch_run`, minus the agent."""
    state = SimpleNamespace(calls=[])

    def _fake(prompt, **kwargs):
        state.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(trace_id=f"sdk-fake{len(state.calls)}",
                               add_done_callback=lambda callback: None)

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return state


def _armed(client, title="a task", **body):
    resp = client.post("/api/tasks", json={"title": title, **body})
    assert resp.status_code == 201, resp.get_json()
    task_id = resp.get_json()["task"]["id"]
    assert client.post(f"/api/tasks/{task_id}/arm", json={}).status_code == 200
    return task_id


def _link(task_id, key="CAI-224", provider="fake"):
    """A link carrying the snapshot the attach left — the issue not yet done."""
    url = f"https://fake.test/{key}"
    with SessionLocal() as session:
        row = AgentTaskLink(
            task_id=task_id, provider=provider, external_key=key, url=url,
            created_at="2026-01-01 00:00:00")
        session.add(row)
        session.commit()
        link_id = row.id
    seed(provider, key, json.dumps(ExternalIssue(
        provider=provider, key=key, url=url, title=f"{key} at capture",
        state="Todo", state_category=STATE_UNSTARTED).to_dict()))
    return link_id


def _run_id(task_id):
    with SessionLocal() as session:
        return session.exec(
            select(AgentTaskRun)
            .where(AgentTaskRun.task_id == task_id)).first().id


def _snapshot(link_id):
    with SessionLocal() as session:
        row = session.get(AgentTaskLink, link_id)
        provider, key, error = row.provider, row.external_key, row.sync_error
    snapshot_json, synced_at = cached(provider, key)
    return (json.loads(snapshot_json), synced_at, error)


def _launched(client, launcher, title="a task", **body):
    task_id = _armed(client, title, **body)
    assert client.post(f"/api/tasks/{task_id}/run").status_code == 201
    return task_id


def test_the_finish_signal_refreshes_the_linked_issue(flask_client, launcher,
                                                      fake_tracker):
    task_id = _launched(flask_client, launcher)
    link_id = _link(task_id)

    runs.finish_run(_run_id(task_id))

    issue, synced_at, error = _snapshot(link_id)
    assert issue["state"] == "Done"
    assert issue["state_category"] == STATE_COMPLETED
    assert synced_at != "2026-01-01 00:00:00"
    assert error is None
    assert fake_tracker.calls == 1


def test_a_finish_reporting_failure_refreshes_it_too(flask_client, launcher,
                                                     fake_tracker):
    """The same report, with the same reason to run: an agent that gave up
    still says so on the issue, and that comment moved its state."""
    task_id = _launched(flask_client, launcher)
    link_id = _link(task_id)

    runs.finish_run(_run_id(task_id), status="failed", reason="stuck")

    assert _snapshot(link_id)[0]["state"] == "Done"


def test_every_link_the_task_holds_is_refreshed(flask_client, launcher,
                                                fake_tracker):
    task_id = _launched(flask_client, launcher)
    first, second = _link(task_id, "CAI-1"), _link(task_id, "CAI-2")

    runs.finish_run(_run_id(task_id))

    assert _snapshot(first)[0]["state"] == "Done"
    assert _snapshot(second)[0]["state"] == "Done"


def test_an_unreachable_tracker_cannot_unsettle_the_finish(flask_client,
                                                           launcher,
                                                           fake_tracker):
    fake_tracker.error = IntegrationError("tracker is down", retryable=True)
    task_id = _launched(flask_client, launcher)
    link_id = _link(task_id)

    result = runs.finish_run(_run_id(task_id))

    assert result["settled"] is True
    with SessionLocal() as session:
        from lib.orm.models.tasks import AgentTask
        assert session.get(AgentTask, task_id).status == "done"
    issue, synced_at, error = _snapshot(link_id)
    assert error == "tracker is down"
    assert issue["state"] == "Todo"
    assert synced_at == "2026-01-01 00:00:00"


def test_an_unreachable_tracker_still_releases_the_dependents(flask_client,
                                                              launcher,
                                                              fake_tracker):
    fake_tracker.error = IntegrationError("tracker is down", retryable=True)
    blocker = _launched(flask_client, launcher, "blocker")
    _link(blocker)
    dependent = _armed(flask_client, "dependent")
    flask_client.put(f"/api/tasks/{dependent}/deps",
                     json={"depends_on": [blocker]})

    result = runs.finish_run(_run_id(blocker))

    assert result["fired"] == [dependent]


def test_a_replayed_finish_spends_no_second_round_trip(flask_client, launcher,
                                                       fake_tracker):
    """The settle is what earns the refresh. A finish that settled nothing —
    a redelivery, or the run callback arriving after the signal — must not
    re-read someone else's API."""
    task_id = _launched(flask_client, launcher)
    _link(task_id)
    run_id = _run_id(task_id)

    runs.finish_run(run_id)
    runs.finish_run(run_id)

    assert fake_tracker.calls == 1


def test_a_task_with_no_link_reaches_no_tracker(flask_client, launcher,
                                                fake_tracker):
    task_id = _launched(flask_client, launcher)

    runs.finish_run(_run_id(task_id))

    assert fake_tracker.calls == 0


def test_a_link_whose_provider_is_gone_does_not_break_the_finish(
        flask_client, launcher, fake_tracker):
    """A tracker switched off in settings leaves its links behind. The finish
    is a statement about the work and cannot depend on that."""
    task_id = _launched(flask_client, launcher)
    _link(task_id, provider="retired")

    result = runs.finish_run(_run_id(task_id))

    assert result["settled"] is True
