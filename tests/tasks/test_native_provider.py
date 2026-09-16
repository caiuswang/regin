"""The native provider — regin answering about its own tasks (CAI-265).

Everything here is a local read. There is no credential, no settings flag and
no network call, so the two failure modes a remote provider has — *not
configured* and *unreachable* — are both unreachable on this one, and the
tests say so by name.
"""

from __future__ import annotations

import pytest

from lib.orm import SessionLocal
from lib.orm.models.tasks import (
    AgentTask, TASK_STATUS_ARCHIVED, TASK_STATUS_ARMED, TASK_STATUS_DONE,
    TASK_STATUS_FAILED, TASK_STATUS_IDEA, TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
)
from lib.tasks.integrations import IntegrationError, registry
from lib.tasks.integrations.base import ExternalRef, IssueTrackerProvider
from lib.tasks.integrations.regin import ReginProvider


@pytest.fixture
def native():
    return ReginProvider()


def _ref(task_id: int) -> ExternalRef:
    return ExternalRef(provider="regin", key=f"regin:{task_id}",
                       url=f"/tasks/{task_id}")


def test_the_native_provider_is_configured_without_any_setting(native):
    assert native.is_configured() is True


def test_there_is_no_native_enabled_flag():
    """A switch able to turn the local provider off would let an operator
    break constraint 1 — a feature whose tracker stopped existing."""
    from lib.settings import settings

    fields = set(type(settings.task_integrations).model_fields)
    assert not [name for name in fields if "regin" in name or "native" in name]


@pytest.mark.parametrize("status,category", [
    (TASK_STATUS_IDEA, "backlog"),
    (TASK_STATUS_ARMED, "unstarted"),
    (TASK_STATUS_QUEUED, "unstarted"),
    (TASK_STATUS_RUNNING, "started"),
    # `done` is `started`, not `completed` — corrected after the escape it
    # caused. `status` is the TASK lifecycle and `complete_run` writes `done`
    # onto it when a run settles, so reading it as `completed` let a run
    # settling ship a feature nobody claimed.
    (TASK_STATUS_DONE, "started"),
    (TASK_STATUS_FAILED, "started"),
    (TASK_STATUS_ARCHIVED, "canceled"),
])
def test_each_task_status_maps_to_its_state_category(
        native, native_task, status, category):
    task_id = native_task(status=status)
    assert native.fetch_issue(_ref(task_id)).state_category == category


def test_only_a_recorded_claim_reads_completed(native, native_task):
    """The other half of the map: `completed` comes from `claimed_at`."""
    claimed = native_task(status=TASK_STATUS_DONE, claimed=True)
    assert native.fetch_issue(_ref(claimed)).state_category == "completed"


def test_archiving_outranks_a_standing_claim(native, native_task):
    """Filing a feature away is a decision about the row; an archived feature
    is not one anybody is still calling shipped."""
    filed = native_task(status=TASK_STATUS_ARCHIVED, claimed=True)
    assert native.fetch_issue(_ref(filed)).state_category == "canceled"


def test_a_failed_task_reads_started_not_unstarted(native, native_task):
    """A feature whose every child failed is being built, not specified."""
    task_id = native_task(status=TASK_STATUS_FAILED)
    assert native.fetch_issue(_ref(task_id)).state_category == "started"


def test_an_archived_task_reads_canceled_not_unknown(native, native_task):
    """`unknown` is reserved for a vocabulary we cannot map; we know this one
    exactly."""
    task_id = native_task(status=TASK_STATUS_ARCHIVED)
    assert native.fetch_issue(_ref(task_id)).state_category == "canceled"


def test_an_idea_reads_backlog_and_an_armed_task_reads_unstarted(
        native, native_task):
    idea = native_task(status=TASK_STATUS_IDEA)
    armed = native_task(status=TASK_STATUS_ARMED)
    assert native.fetch_issue(_ref(idea)).state_category == "backlog"
    assert native.fetch_issue(_ref(armed)).state_category == "unstarted"


def test_the_native_assignee_resolves_to_a_display_name(native, native_task):
    from lib.orm.models.users import User
    from lib.orm import AuthSessionLocal

    with AuthSessionLocal() as session:
        user = User(username="caius", display_name="caius wang",
                    password_hash="x", role="admin")
        session.add(user)
        session.commit()
        session.refresh(user)
        user_id = user.id

    task_id = native_task(assignee=user_id)
    assert native.fetch_issue(_ref(task_id)).assignee == "caius wang"


def test_an_unresolvable_assignee_yields_none_not_a_placeholder(
        native, native_task):
    task_id = native_task(assignee=99999)
    assert native.fetch_issue(_ref(task_id)).assignee is None


def test_extra_carries_only_the_project_name(native, native_task):
    from lib.orm import SessionLocal as _S
    from lib.orm.models.sync import Repo

    with _S() as session:
        repo = Repo(name="regin", path="/tmp/regin")
        session.add(repo)
        session.commit()
        session.refresh(repo)
        repo_id = repo.id

    task_id = native_task(project_id=repo_id)
    assert native.fetch_issue(_ref(task_id)).extra == {"Project": "regin"}

    unfiled = native_task()
    assert native.fetch_issue(_ref(unfiled)).extra == {}


def test_a_missing_task_id_is_a_non_retryable_integration_error(native):
    with pytest.raises(IntegrationError) as caught:
        native.fetch_issue(_ref(999))
    assert "no task 999" in str(caught.value)
    assert caught.value.retryable is False


def test_the_native_provider_never_reports_retryable(native):
    """A local read cannot be unreachable, so nothing here may say it was."""
    with pytest.raises(IntegrationError) as caught:
        native.fetch_issue(_ref(4242))
    assert caught.value.retryable is False
    from lib.tasks.integrations import NotConfigured
    assert not isinstance(caught.value, NotConfigured)


def test_the_native_provider_reads_the_task_row_not_the_api_projection(
        native, native_task, monkeypatch):
    """`fetch_issue` reads `AgentTask` directly; a `store` read would put a
    serializer between the provider and the fact."""
    from lib.tasks import store

    def _explode(*args, **kwargs):
        raise AssertionError("the provider went through lib.tasks.store")

    monkeypatch.setattr(store, "get_task", _explode)
    task_id = native_task(title="read me")
    assert native.fetch_issue(_ref(task_id)).title == "read me"


def test_stage_source_has_no_default_provider():
    """The map's one live constraint-2 violation: a module outside a provider
    naming `linear` as the fallback."""
    from lib.features import stage_source

    assert not hasattr(stage_source, "DEFAULT_PROVIDER")
    assert "linear" not in stage_source.__file__ or True
    source = open(stage_source.__file__).read()
    assert '"linear"' not in source and "'linear'" not in source


def test_the_no_tracker_configured_message_branch_is_gone():
    """Unreachable now: the native provider is always configured, so the
    registry can never report that nothing is."""
    from lib.tasks.integrations import registry as reg

    assert "no issue tracker is configured" not in open(reg.__file__).read()


def test_the_no_tracker_fixture_also_drops_the_native_builder(no_tracker):
    """The 38 call sites that mean *nothing answers* keep meaning it."""
    assert registry.configured_providers() == []


def test_the_provider_row_is_always_on_and_contributes_no_settings(native):
    rows = {row["provider"]: row for row in registry.provider_rows()}
    assert rows["regin"]["configured"] is True
    assert rows["regin"]["ref_hint"] == (
        "a regin task ref like regin:42, or the /tasks/42 URL")


def test_the_base_provider_is_not_live_unless_it_says_so():
    assert IssueTrackerProvider.stage_is_live is False
    assert ReginProvider.stage_is_live is True


def test_the_native_issue_carries_the_task_body_and_labels(native, native_task):
    task_id = native_task(title="t", body="a body", tags_json='["one", "two"]')
    issue = native.fetch_issue(_ref(task_id))
    assert issue.description == "a body"
    assert issue.labels == ("one", "two")
    assert issue.url == f"/tasks/{task_id}"


def test_the_native_issue_passes_priority_through(native, native_task):
    task_id = native_task()
    with SessionLocal() as session:
        row = session.get(AgentTask, task_id)
        row.priority = "high"
        session.add(row)
        session.commit()
    assert native.fetch_issue(_ref(task_id)).priority == "high"


# ── create_issue: a defect's fix item as a child task (regin:79) ─────


def test_create_issue_files_a_child_task_under_the_parent(native, native_task):
    parent = native_task(title="the feature")

    ref = native.create_issue(parent=_ref(parent), title="it broke",
                              body="regin defect #1")

    with SessionLocal() as session:
        row = session.get(AgentTask, int(ref.key.split(":")[1]))
    assert ref.provider == "regin"
    assert ref.key == f"regin:{row.id}"
    assert ref.url == f"/tasks/{row.id}"
    assert row.parent_id == parent
    assert row.title == "it broke"
    assert row.body == "regin defect #1"
    assert row.status == TASK_STATUS_IDEA


def test_create_issue_under_a_missing_parent_is_not_retryable(native):
    with pytest.raises(IntegrationError) as caught:
        native.create_issue(parent=_ref(9999), title="x", body="y")
    assert caught.value.retryable is False

    with pytest.raises(IntegrationError) as caught:
        native.create_issue(parent=ExternalRef(provider="regin", key="ENG-1",
                                               url=""), title="x", body="y")
    assert caught.value.retryable is False
