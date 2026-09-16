"""The `<tracker>` block a linked task launches with.

The claim under test is that a link is carried *forward* into the work, not
just pulled once at capture time: a run whose task names a tracker issue opens
with that issue named, so the agent knows there is a tracker to read and to
record against. A task with no link gains nothing — the block is absent, not
empty.

Nothing here reaches a tracker. Links are written straight into
`agent_task_links` with the snapshot a fetch would have left, because what is
being pinned is the prompt's shape, not the adapter's.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lib.orm import SessionLocal
from tests.issue_cache_seed import seed
from lib.orm.models.tasks import AgentTaskLink
from lib.tasks import runs
from lib.tasks.integrations import registry
from lib.tasks.integrations.base import (
    ExternalIssue, ExternalRef, IssueTrackerProvider, STATE_UNSTARTED,
)

DESCRIPTION = "the issue body, as the tracker currently holds it"


class FakeTracker(IssueTrackerProvider):
    """A second platform, to prove the block names a provider by asking the
    registry rather than by knowing what Linear is called."""

    provider_id = "fake"
    display_name = "Fake Tracker"
    ref_hint = "a fake:// reference"

    def is_configured(self) -> bool:
        return True

    def parse_ref(self, text: str):
        return None

    def fetch_issue(self, ref: ExternalRef) -> ExternalIssue:
        raise AssertionError("the prompt must not fetch")


@pytest.fixture
def launcher(monkeypatch):
    """`launch_run`, minus the agent — the prompt is read back off the call."""
    state = SimpleNamespace(calls=[])

    def _fake(prompt, **kwargs):
        state.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(trace_id="sdk-fake0",
                               add_done_callback=lambda callback: None)

    monkeypatch.setattr(runs.supervisor, "launch_run", _fake)
    return state


@pytest.fixture
def fake_tracker(monkeypatch):
    monkeypatch.setitem(registry._PROVIDER_BUILDERS, "fake", FakeTracker)
    return FakeTracker


def _capture(client, title="a task", **body):
    resp = client.post("/api/tasks", json={"title": title, **body})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["task"]["id"]


def _armed(client, title="a task", **body):
    task_id = _capture(client, title, **body)
    assert client.post(f"/api/tasks/{task_id}/arm", json={}).status_code == 200
    return task_id


def _link(task_id, key="CAI-223", provider="linear", *, synced=True,
          title=None, state="Todo"):
    """One `agent_task_links` row, with or without the snapshot a fetch leaves."""
    url = f"https://linear.app/acme/issue/{key}/wire-the-thing"
    snapshot = None
    if synced:
        snapshot = json.dumps(ExternalIssue(
            provider=provider, key=key, url=url,
            title=title or f"{key} as the tracker titles it", state=state,
            state_category=STATE_UNSTARTED,
            description=DESCRIPTION).to_dict())
    with SessionLocal() as session:
        session.add(AgentTaskLink(
            task_id=task_id, provider=provider, external_key=key, url=url,
            sync_error=None if synced else "linear is unreachable",
            created_at="2026-01-01 00:00:00"))
        session.commit()
    if snapshot is not None:
        seed(provider, key, snapshot)
    return url


def _launch(client, launcher, task_id) -> str:
    resp = client.post(f"/api/tasks/{task_id}/run")
    assert resp.status_code == 201, resp.get_json()
    return launcher.calls[0]["prompt"]


def _block(prompt: str) -> str:
    assert "<tracker>" in prompt, prompt
    return prompt.split("<tracker>", 1)[1].split("</tracker>", 1)[0]


def test_the_launch_prompt_names_the_linked_issue(flask_client, launcher):
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    url = _link(task_id)

    block = _block(_launch(flask_client, launcher, task_id))

    assert "CAI-223" in block
    assert "CAI-223 as the tracker titles it" in block
    assert "Todo" in block
    assert url in block


def test_a_task_with_no_link_gets_no_tracker_block(flask_client, launcher):
    task_id = _armed(flask_client, "shelf", body="wire the thing")

    prompt = _launch(flask_client, launcher, task_id)

    assert "<tracker>" not in prompt
    assert prompt.startswith("wire the thing")


def test_the_tracker_block_states_the_issue_outranks_the_snapshot(
        flask_client, launcher):
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    _link(task_id)

    block = _block(_launch(flask_client, launcher, task_id)).lower()

    assert "source of truth" in block
    assert "comment" in block


def test_the_tracker_block_precedes_the_finish_block(flask_client, launcher):
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    _link(task_id)

    prompt = _launch(flask_client, launcher, task_id)

    assert prompt.index("<tracker>") < prompt.index("<finish>")
    assert prompt.startswith("wire the thing")


def test_a_maximal_body_cannot_truncate_the_tracker_block(flask_client,
                                                          launcher):
    task_id = _armed(flask_client, "big", body="x" * runs.PROMPT_MAX)
    _link(task_id)

    prompt = _launch(flask_client, launcher, task_id)

    assert "CAI-223" in _block(prompt)
    assert runs._finish_command(1) in prompt


def test_the_tracker_block_names_every_linked_issue(flask_client, launcher):
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    _link(task_id, "CAI-223")
    _link(task_id, "CAI-224")

    block = _block(_launch(flask_client, launcher, task_id))

    assert "CAI-223" in block and "CAI-224" in block


def test_a_link_that_never_synced_is_still_named(flask_client, launcher):
    """The link is the operator's assertion; a tracker being down does not
    withdraw it, so the key and URL they gave still reach the agent."""
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    url = _link(task_id, "CAI-999", synced=False)

    block = _block(_launch(flask_client, launcher, task_id))

    assert "CAI-999" in block and url in block


def test_the_tracker_block_names_the_provider_by_display_name(
        flask_client, launcher, fake_tracker):
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    _link(task_id, "FAKE-1", provider="fake")

    block = _block(_launch(flask_client, launcher, task_id))

    assert "Fake Tracker" in block


def test_the_tracker_block_does_not_repeat_the_issue_description(
        flask_client, launcher):
    """The composer already offered the description as the task's body. A
    second copy in the prompt is either duplication or a stale contradiction
    of one."""
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    _link(task_id)

    prompt = _launch(flask_client, launcher, task_id)

    assert DESCRIPTION not in prompt


# ── the order the two blocks ask for ─────────────────────────
#
# The finish signal is what makes regin re-read the issue, and the agent is
# the one that moves its state. An agent that reports first and records
# afterwards leaves regin holding the state from before its own work — the
# card the operator opens says Todo about work that is done. So the prompt
# states the order, and states why, at both places the agent reads.

def _finish(prompt: str) -> str:
    assert "<finish>" in prompt, prompt
    return prompt.split("<finish>", 1)[1].split("</finish>", 1)[0]


def test_the_tracker_block_puts_the_issue_update_before_the_finish_command(
        flask_client, launcher):
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    _link(task_id)

    block = _block(_launch(flask_client, launcher, task_id)).lower()

    assert "before you run the finish command" in block
    assert "afterwards" in block


def test_the_finish_block_of_a_linked_task_names_the_update_as_a_precondition(
        flask_client, launcher):
    task_id = _armed(flask_client, "shelf", body="wire the thing")
    _link(task_id)

    block = _finish(_launch(flask_client, launcher, task_id)).lower()

    assert "before you run it" in block
    assert "issue" in block


def test_an_unlinked_task_is_told_nothing_about_a_tracker(flask_client,
                                                          launcher):
    """The unlinked prompt is exactly what it was: there is no issue to
    update, and an instruction about one is noise the agent has to resolve."""
    task_id = _armed(flask_client, "shelf", body="wire the thing")

    block = _finish(_launch(flask_client, launcher, task_id)).lower()

    assert "issue" not in block
    assert "before you run it" not in block


def test_the_ordering_sentence_survives_a_maximal_body(flask_client, launcher):
    task_id = _armed(flask_client, "big", body="x" * runs.PROMPT_MAX)
    _link(task_id)

    prompt = _launch(flask_client, launcher, task_id)

    assert "before you run it" in _finish(prompt).lower()
    assert runs._finish_command(1) in prompt
