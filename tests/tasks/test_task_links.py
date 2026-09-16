"""`agent_task_links` — the join between a task and a third-party tracker.

Two claims are under test. The first is that the *seam* holds: a fake provider
registered next to Linear is resolved by its own `parse_ref`, so nothing
outside a provider module branches on a platform name and the second tracker
is an additive change. The second is that a link is the operator's assertion:
a fetch that fails records the reason and keeps the link (and its previous
snapshot) rather than unwinding the attach or 500ing the page.

No test reaches the network. Linear's adapter is exercised against its own
normalizer and its URL parser, which is where every vendor-shaped decision
lives.
"""

from __future__ import annotations

import json
from typing import Optional

import pytest
from sqlmodel import select

from lib.auth import create_token
from lib.orm import SessionLocal
from tests.issue_cache_seed import cached, seed
from lib.orm.models.tasks import AgentTaskLink
from lib.settings import TaskIntegrationsConfig, settings
from lib.tasks import links
from lib.tasks.integrations import registry
from lib.tasks.integrations.base import (
    ExternalIssue, ExternalRef, IntegrationError, IssueTrackerProvider,
    STATE_STARTED, STATE_UNKNOWN,
)
from lib.tasks.integrations.linear import LinearProvider

EDITOR = {"Authorization": f"Bearer {create_token(2, 'ed', 'editor')}"}
VIEWER = {"Authorization": f"Bearer {create_token(3, 'vw', 'viewer')}"}


class FakeTracker(IssueTrackerProvider):
    """A second platform, to prove the registry routes by claim not by name."""

    provider_id = "fake"
    display_name = "Fake Tracker"
    ref_hint = "a fake:// reference"
    configured = True
    issue: Optional[ExternalIssue] = None
    error: Optional[Exception] = None
    calls = 0

    def is_configured(self) -> bool:
        return type(self).configured

    def parse_ref(self, text: str) -> Optional[ExternalRef]:
        raw = (text or "").strip()
        if not raw.startswith("fake://"):
            return None
        key = raw.split("fake://", 1)[1]
        return ExternalRef(provider=self.provider_id, key=key,
                           url=f"https://fake.test/{key}")

    def fetch_issue(self, ref: ExternalRef) -> ExternalIssue:
        type(self).calls += 1
        if type(self).error is not None:
            raise type(self).error
        return type(self).issue or ExternalIssue(
            provider=self.provider_id, key=ref.key, url=ref.url,
            title=f"fake {ref.key}", state="In Progress",
            state_category=STATE_STARTED, assignee="Ada",
            labels=("bug",), description="from the fake tracker")


@pytest.fixture
def fake_tracker(monkeypatch):
    monkeypatch.setitem(registry._PROVIDER_BUILDERS, "fake", FakeTracker)
    FakeTracker.configured = True
    FakeTracker.issue = None
    FakeTracker.error = None
    FakeTracker.calls = 0
    yield FakeTracker
    FakeTracker.error = None


@pytest.fixture
def linear_on(monkeypatch):
    monkeypatch.setattr(
        settings, "task_integrations",
        TaskIntegrationsConfig(linear_enabled=True, linear_api_key="lin_key"))
    return settings.task_integrations


@pytest.fixture
def linear_off(monkeypatch):
    monkeypatch.setattr(settings, "task_integrations",
                        TaskIntegrationsConfig())
    return settings.task_integrations


def _task(client, title="link me"):
    resp = client.post("/api/tasks", json={"title": title}, headers=EDITOR)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["task"]["id"]


def _rows(task_id):
    with SessionLocal() as session:
        return session.exec(
            select(AgentTaskLink)
            .where(AgentTaskLink.task_id == task_id)).all()


# ── the Linear adapter's own vendor shapes ───────────────────

@pytest.mark.parametrize("text,key", [
    ("https://linear.app/acme/issue/ENG-42/wire-the-thing", "ENG-42"),
    ("https://linear.app/acme/issue/ENG-42", "ENG-42"),
    ("https://linear.app/acme/issue/eng-42/slug?foo=1#c", "ENG-42"),
    ("ENG-42", "ENG-42"),
    ("  eng-42  ", "ENG-42"),
    # A workspace slug is user-chosen and routinely ends in digits. Reading
    # the key from anywhere but the segment after `/issue/` claims `TEAM-1`
    # here and silently links a different, existing issue.
    ("https://linear.app/my-team-1/issue/ENG-42/fix-it", "ENG-42"),
    ("https://linear.app/acme-2/issue/ENG-42", "ENG-42"),
])
def test_linear_claims_its_urls_and_identifiers(linear_on, text, key):
    ref = LinearProvider().parse_ref(text)
    assert ref is not None and ref.key == key and ref.provider == "linear"


def test_a_pasted_url_is_kept_verbatim_so_it_still_resolves(linear_on):
    """The workspace-qualified URL is the one that opens. Rebuilding it from
    the key alone strands the card on exactly the path — a failed fetch —
    where the stored URL is all the operator has."""
    pasted = "https://linear.app/acme/issue/ENG-42/wire-the-thing"
    assert LinearProvider().parse_ref(pasted).url == pasted
    assert LinearProvider().parse_ref("ENG-42").url.endswith("/issue/ENG-42")


def test_a_linear_url_whose_segment_is_not_a_key_is_declined(linear_on):
    assert LinearProvider().parse_ref(
        "https://linear.app/acme/issue/not-a-key/slug") is None


@pytest.mark.parametrize("text", [
    "", "   ", "not a reference",
    "https://github.com/acme/repo/issues/42",
    # A bare key inside prose is NOT claimed: `ABC-1` is also a Jira key, and
    # a greedy claim here would make the next adapter unreachable.
    "see ENG-42 for details",
])
def test_linear_declines_what_it_does_not_own(linear_on, text):
    assert LinearProvider().parse_ref(text) is None


def test_linear_normalizes_a_graphql_node_into_the_canonical_shape(linear_on):
    provider = LinearProvider()
    node = {
        "identifier": "ENG-7", "title": "ship it",
        "description": "body", "url": "https://linear.app/acme/issue/ENG-7",
        "updatedAt": "2026-01-02T03:04:05.000Z", "priority": 2,
        "state": {"name": "In Progress", "type": "started"},
        "assignee": {"displayName": "Ada"},
        "labels": {"nodes": [{"name": "bug"}, {"name": "ui"}]},
        "team": {"key": "ENG", "name": "Engineering"},
        "project": {"name": "Q1"},
    }
    issue = provider._to_issue(
        ExternalRef("linear", "ENG-7", "https://x"), node)

    assert issue.state_category == STATE_STARTED
    assert issue.priority == "high"          # Linear's 2, in regin's words
    assert issue.assignee == "Ada"
    assert issue.labels == ("bug", "ui")
    assert issue.extra == {"Team": "Engineering", "Project": "Q1"}


def test_an_unknown_linear_state_type_falls_back_to_unknown(linear_on):
    issue = LinearProvider()._to_issue(
        ExternalRef("linear", "ENG-8", "https://x"),
        {"identifier": "ENG-8", "title": "t",
         "state": {"name": "Weird", "type": "brand-new-category"}})
    assert issue.state_category == STATE_UNKNOWN


def test_an_unconfigured_linear_claims_nothing_and_refuses_to_fetch(linear_off):
    """Linear is absent from the configured set — not the whole set empty.

    regin's own tracker holds no credential and is therefore always
    configured, so "nothing is configured" stopped being a reachable state.
    What this still proves is the thing that matters: an unconfigured provider
    is invisible to a paste rather than a source of runtime 500s.
    """
    provider = LinearProvider()
    assert provider.is_configured() is False
    assert "linear" not in {p.provider_id
                            for p in registry.configured_providers()}
    with pytest.raises(IntegrationError):
        provider.fetch_issue(ExternalRef("linear", "ENG-1", "https://x"))


# ── the registry seam ────────────────────────────────────────

def test_the_registry_routes_a_second_platform_by_its_own_claim(
        fake_tracker, linear_on):
    provider, ref = registry.resolve_ref("fake://X-1")
    assert provider.provider_id == "fake" and ref.key == "X-1"

    provider, ref = registry.resolve_ref("https://linear.app/a/issue/ENG-3/s")
    assert provider.provider_id == "linear" and ref.key == "ENG-3"


def test_an_unclaimed_reference_names_what_each_tracker_wants(
        fake_tracker, linear_on):
    with pytest.raises(registry.UnknownRef) as exc:
        registry.resolve_ref("https://example.com/whatever")
    assert "Linear" in str(exc.value) and "Fake Tracker" in str(exc.value)


def test_two_claimants_are_refused_rather_than_guessed_at(monkeypatch,
                                                          linear_on):
    class AlsoClaimsKeys(FakeTracker):
        provider_id = "also"
        display_name = "Also"

        def parse_ref(self, text):
            return ExternalRef(self.provider_id, "ENG-42", "https://also/42")

    monkeypatch.setitem(registry._PROVIDER_BUILDERS, "also", AlsoClaimsKeys)
    with pytest.raises(registry.AmbiguousRef) as exc:
        registry.resolve_ref("ENG-42")
    assert set(exc.value.candidates) == {"linear", "also"}


def test_an_explicit_provider_skips_resolution(fake_tracker, linear_on):
    provider, ref = registry.resolve_ref("fake://X-9", provider_id="fake")
    assert provider.provider_id == "fake" and ref.key == "X-9"

    with pytest.raises(registry.UnknownRef):
        registry.resolve_ref("fake://X-9", provider_id="linear")


def test_provider_rows_list_unconfigured_platforms_too(linear_off):
    rows = {row["provider"]: row for row in registry.provider_rows()}
    assert rows["linear"]["configured"] is False
    assert rows["linear"]["display_name"] == "Linear"


# ── the store ────────────────────────────────────────────────

def test_attaching_snapshots_the_issue(tmp_db, flask_client, fake_tracker):
    task_id = _task(flask_client)
    link = links.attach(task_id, "fake://X-1")

    assert link["provider"] == "fake" and link["external_key"] == "X-1"
    assert link["issue"]["state_category"] == STATE_STARTED
    assert link["issue"]["assignee"] == "Ada"
    assert link["sync_error"] is None and link["synced_at"]


def test_reattaching_the_same_issue_is_idempotent(tmp_db, flask_client,
                                                  fake_tracker):
    task_id = _task(flask_client)
    first = links.attach(task_id, "fake://X-1")
    second = links.attach(task_id, "fake://X-1")

    assert first["id"] == second["id"]
    assert len(_rows(task_id)) == 1


def test_a_failed_fetch_keeps_the_link_and_records_why(tmp_db, flask_client,
                                                       fake_tracker):
    fake_tracker.error = IntegrationError("tracker is down", retryable=True)
    task_id = _task(flask_client)
    link = links.attach(task_id, "fake://X-2")

    assert link["sync_error"] == "tracker is down"
    assert link["issue"] is None and link["synced_at"] is None
    assert len(_rows(task_id)) == 1


def test_a_failed_refresh_leaves_the_previous_snapshot_readable(
        tmp_db, flask_client, fake_tracker):
    task_id = _task(flask_client)
    link = links.attach(task_id, "fake://X-3")
    synced_at = link["synced_at"]

    fake_tracker.error = IntegrationError("tracker is down", retryable=True)
    stale = links.refresh(task_id, link["id"])

    assert stale["sync_error"] == "tracker is down"
    assert stale["issue"]["title"] == "fake X-3"
    assert stale["synced_at"] == synced_at


def test_refresh_and_detach_refuse_another_tasks_link(tmp_db, flask_client,
                                                      fake_tracker):
    mine = _task(flask_client, "mine")
    theirs = _task(flask_client, "theirs")
    link = links.attach(mine, "fake://X-4")

    assert links.refresh(theirs, link["id"]) is None
    assert links.detach(theirs, link["id"]) is False
    assert len(_rows(mine)) == 1


def test_a_corrupt_snapshot_renders_as_no_issue_rather_than_raising(
        tmp_db, flask_client, fake_tracker):
    task_id = _task(flask_client)
    links.attach(task_id, "fake://X-5")
    seed("fake", "X-5", "{not json")

    assert links.list_links(task_id)[0]["issue"] is None


# ── the HTTP surface ─────────────────────────────────────────

def test_the_attach_route_round_trips(tmp_db, flask_client, fake_tracker):
    task_id = _task(flask_client)
    resp = flask_client.post(f"/api/tasks/{task_id}/links",
                             json={"ref": "fake://X-6"}, headers=EDITOR)
    assert resp.status_code == 201, resp.get_json()

    listed = flask_client.get(f"/api/tasks/{task_id}/links", headers=VIEWER)
    assert [l["external_key"] for l in listed.get_json()["links"]] == ["X-6"]


def test_the_attach_route_reads_nothing_from_the_remote(tmp_db, flask_client,
                                                        fake_tracker):
    """A list read must never spend a round trip — that is what stops an open
    task page from becoming a poll against someone else's API."""
    task_id = _task(flask_client)
    flask_client.post(f"/api/tasks/{task_id}/links",
                      json={"ref": "fake://X-7"}, headers=EDITOR)
    before = fake_tracker.calls

    flask_client.get(f"/api/tasks/{task_id}/links", headers=VIEWER)
    flask_client.get(f"/api/tasks/{task_id}/links", headers=VIEWER)
    assert fake_tracker.calls == before

    flask_client.post(f"/api/tasks/{task_id}/links/"
                      f"{_rows(task_id)[0].id}/refresh", headers=EDITOR)
    assert fake_tracker.calls == before + 1


def test_an_unresolvable_reference_is_a_400_naming_the_field(
        tmp_db, flask_client, linear_off):
    task_id = _task(flask_client)
    resp = flask_client.post(f"/api/tasks/{task_id}/links",
                             json={"ref": "ENG-42"}, headers=EDITOR)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "ref"
    assert _rows(task_id) == []


def test_an_ambiguous_reference_is_a_409_listing_the_candidates(
        monkeypatch, tmp_db, flask_client, linear_on):
    class AlsoClaimsKeys(FakeTracker):
        provider_id = "also"

        def parse_ref(self, text):
            return ExternalRef(self.provider_id, "ENG-42", "https://also/42")

    monkeypatch.setitem(registry._PROVIDER_BUILDERS, "also", AlsoClaimsKeys)
    task_id = _task(flask_client)
    resp = flask_client.post(f"/api/tasks/{task_id}/links",
                             json={"ref": "ENG-42"}, headers=EDITOR)
    assert resp.status_code == 409
    assert set(resp.get_json()["candidates"]) == {"linear", "also"}


def test_an_empty_ref_is_rejected_before_any_provider_runs(tmp_db,
                                                           flask_client,
                                                           fake_tracker):
    task_id = _task(flask_client)
    resp = flask_client.post(f"/api/tasks/{task_id}/links",
                             json={"ref": "   "}, headers=EDITOR)
    assert resp.status_code == 400
    assert fake_tracker.calls == 0


def test_links_on_an_unknown_task_are_a_404(tmp_db, flask_client,
                                            fake_tracker):
    assert flask_client.get("/api/tasks/9999/links",
                            headers=VIEWER).status_code == 404
    assert flask_client.post("/api/tasks/9999/links",
                             json={"ref": "fake://X-8"},
                             headers=EDITOR).status_code == 404


def test_detach_removes_the_row_and_a_replay_404s(tmp_db, flask_client,
                                                  fake_tracker):
    task_id = _task(flask_client)
    link_id = links.attach(task_id, "fake://X-9")["id"]

    first = flask_client.delete(f"/api/tasks/{task_id}/links/{link_id}",
                                headers=EDITOR)
    assert first.status_code == 200 and _rows(task_id) == []
    second = flask_client.delete(f"/api/tasks/{task_id}/links/{link_id}",
                                 headers=EDITOR)
    assert second.status_code == 404


def test_a_viewer_may_read_links_but_not_write_them(tmp_db, flask_client,
                                                    fake_tracker):
    task_id = _task(flask_client)
    links.attach(task_id, "fake://X-10")

    assert flask_client.get(f"/api/tasks/{task_id}/links",
                            headers=VIEWER).status_code == 200
    assert flask_client.post(f"/api/tasks/{task_id}/links",
                             json={"ref": "fake://X-11"},
                             headers=VIEWER).status_code == 403


def test_the_integrations_route_reports_configuration(tmp_db, flask_client,
                                                      linear_off):
    resp = flask_client.get("/api/task-integrations", headers=VIEWER)
    assert resp.status_code == 200
    rows = {r["provider"]: r for r in resp.get_json()["providers"]}
    assert rows["linear"]["configured"] is False
    assert rows["linear"]["ref_hint"]


def test_the_snapshot_is_stored_as_the_canonical_dict(tmp_db, flask_client,
                                                      fake_tracker):
    task_id = _task(flask_client)
    links.attach(task_id, "fake://X-12")
    stored = json.loads(cached("fake", "X-12")[0])
    assert set(stored) == {"provider", "key", "url", "title", "state",
                           "state_category", "priority", "assignee", "labels",
                           "description", "updated_at", "rounds", "extra"}


def test_a_link_detached_mid_fetch_is_reported_not_crashed(
        tmp_db, flask_client, fake_tracker, monkeypatch):
    """`attach` commits the row, closes the session, then spends a network
    round trip. A DELETE inside that window used to leave `_sync` writing to
    None — an unhandled 500 on a request the operator did nothing wrong in."""
    task_id = _task(flask_client)
    created: list[int] = []

    real_fetch = FakeTracker.fetch_issue

    def vanish(self, ref):
        row = _rows(task_id)[0]
        created.append(row.id)
        with SessionLocal() as session:
            session.delete(session.get(AgentTaskLink, row.id))
            session.commit()
        return real_fetch(self, ref)

    monkeypatch.setattr(FakeTracker, "fetch_issue", vanish)
    assert links.attach(task_id, "fake://X-13") is None
    assert created and _rows(task_id) == []


def test_the_attach_route_404s_when_the_link_vanished_mid_fetch(
        tmp_db, flask_client, fake_tracker, monkeypatch):
    task_id = _task(flask_client)
    monkeypatch.setattr(links, "_sync", lambda *a, **k: None)
    resp = flask_client.post(f"/api/tasks/{task_id}/links",
                             json={"ref": "fake://X-14"}, headers=EDITOR)
    assert resp.status_code == 404


def test_a_replay_upgrades_the_url_of_a_link_that_never_synced(
        tmp_db, flask_client, fake_tracker):
    """A bare identifier can only infer a URL. If the operator later pastes
    the real one while the tracker is down, the never-synced row must adopt
    it — otherwise the card's out-link stays the guess forever."""
    fake_tracker.error = IntegrationError("down", retryable=True)
    task_id = _task(flask_client)
    first = links.attach(task_id, "fake://X-15")

    class RicherUrl(FakeTracker):
        def parse_ref(self, text):
            ref = FakeTracker.parse_ref(self, text)
            return None if ref is None else ExternalRef(
                ref.provider, ref.key, "https://fake.test/team/X-15")

    registry._PROVIDER_BUILDERS["fake"] = RicherUrl
    try:
        second = links.attach(task_id, "fake://X-15")
    finally:
        registry._PROVIDER_BUILDERS["fake"] = FakeTracker

    assert second["id"] == first["id"]
    assert second["url"] == "https://fake.test/team/X-15"
    assert len(_rows(task_id)) == 1


def test_a_replay_does_not_clobber_a_url_the_tracker_canonicalized(
        tmp_db, flask_client, fake_tracker):
    task_id = _task(flask_client)
    synced = links.attach(task_id, "fake://X-16")
    assert synced["synced_at"]

    class OtherUrl(FakeTracker):
        def parse_ref(self, text):
            ref = FakeTracker.parse_ref(self, text)
            return None if ref is None else ExternalRef(
                ref.provider, ref.key, "https://fake.test/guessed")

    fake_tracker.error = IntegrationError("down", retryable=True)
    registry._PROVIDER_BUILDERS["fake"] = OtherUrl
    try:
        again = links.attach(task_id, "fake://X-16")
    finally:
        registry._PROVIDER_BUILDERS["fake"] = FakeTracker

    assert again["url"] == synced["url"]


# ── preview: reading an issue before a task exists ───────────

def _every_link_row():
    with SessionLocal() as session:
        return session.exec(select(AgentTaskLink)).all()


def test_preview_returns_issue_and_creates_no_link(tmp_db, flask_client,
                                                   fake_tracker):
    """The composer reads an issue before the task it will hang off exists.

    Nothing is written: a dialog that is closed without capturing must leave
    no link row behind, and no task to hang one off.
    """
    resp = flask_client.post("/api/tasks/links/preview",
                             json={"ref": "fake://P-1"}, headers=EDITOR)

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["provider"] == "fake" and body["key"] == "P-1"
    assert body["issue"]["title"] == "fake P-1"
    assert body["issue"]["description"] == "from the fake tracker"
    assert _every_link_row() == []


def test_preview_ref_required(tmp_db, flask_client, fake_tracker):
    resp = flask_client.post("/api/tasks/links/preview",
                             json={"ref": "   "}, headers=EDITOR)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "ref"
    assert fake_tracker.calls == 0


def test_preview_unknown_ref_is_400(tmp_db, flask_client, linear_off):
    resp = flask_client.post("/api/tasks/links/preview",
                             json={"ref": "ENG-42"}, headers=EDITOR)
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "ref"


def test_preview_ambiguous_ref_is_409_naming_candidates(
        monkeypatch, tmp_db, flask_client, linear_on):
    class AlsoClaimsKeys(FakeTracker):
        provider_id = "also"

        def parse_ref(self, text):
            return ExternalRef(self.provider_id, "ENG-42", "https://also/42")

    monkeypatch.setitem(registry._PROVIDER_BUILDERS, "also", AlsoClaimsKeys)
    resp = flask_client.post("/api/tasks/links/preview",
                             json={"ref": "ENG-42"}, headers=EDITOR)

    assert resp.status_code == 409
    assert set(resp.get_json()["candidates"]) == {"linear", "also"}


def test_preview_fetch_failure_is_400_and_creates_no_link(tmp_db,
                                                          flask_client,
                                                          fake_tracker):
    """A preview has no link row to record the failure against — unlike an
    attach, where the operator's assertion outlives an unreachable tracker."""
    fake_tracker.error = IntegrationError("tracker is down", retryable=True)
    resp = flask_client.post("/api/tasks/links/preview",
                             json={"ref": "fake://P-2"}, headers=EDITOR)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "tracker is down"
    assert _every_link_row() == []


def test_preview_requires_editor(tmp_db, flask_client, fake_tracker):
    resp = flask_client.post("/api/tasks/links/preview",
                             json={"ref": "fake://P-3"}, headers=VIEWER)
    assert resp.status_code == 403
    assert fake_tracker.calls == 0
