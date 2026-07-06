"""CLI smoke tests for the headless proposal-lifecycle commands.

Covers `regin topics propose / proposal-list / proposal-show /
proposal-diff / proposal-apply / proposal-review-state /
proposal-feedback`. The library behavior is tested elsewhere; these pin
that the typer wrappers parse args, dispatch, and report errors with
nonzero exits. Drafting goes through the `stub_proposal_provider`
fixture, which makes `create_proposal_run` synchronous + canned.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from typer.testing import CliRunner

from cli.commands import topics as topics_cmd
from lib.settings import TopicProposalExternalAgent, settings
from lib.topics import bootstrap, load_graph_merged


runner = CliRunner()


@pytest.fixture
def external_agent_config(monkeypatch):
    monkeypatch.setattr(
        settings, "topic_proposal_external_agents",
        {"claude": TopicProposalExternalAgent(command="true")},
    )


def _prepare_repo(fake_git_repo) -> None:
    (fake_git_repo / "service").mkdir(exist_ok=True)
    (fake_git_repo / "service" / "api.py").write_text("import os\n")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "fixture"])
    bootstrap(fake_git_repo)


def _propose(fake_git_repo) -> str:
    result = runner.invoke(topics_cmd.topics_app, [
        "propose", "map the service layer", "--repo", str(fake_git_repo), "--json",
    ])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["id"]


def _mark_ready(fake_git_repo, proposal_id: str) -> None:
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-review-state", proposal_id, "ready_to_apply",
        "--repo", str(fake_git_repo),
    ])
    assert result.exit_code == 0, result.output


# ── propose ────────────────────────────────────────────────────────


def test_propose_runs_synchronously_and_prints_id_and_state(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "propose", "map the service layer", "--repo", str(fake_git_repo),
    ])
    assert result.exit_code == 0, result.output
    assert "Proposal " in result.output
    assert "completed" in result.output


def test_propose_json_reports_id_and_completed_state(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "propose", "map the service layer", "--repo", str(fake_git_repo), "--json",
    ])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["id"]
    assert body["state"] == "completed"


def test_propose_fails_fast_without_external_agent(
        stub_proposal_provider, fake_git_repo, monkeypatch):
    monkeypatch.setattr(settings, "topic_proposal_external_agents", {})
    _prepare_repo(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "propose", "anything", "--repo", str(fake_git_repo),
    ])
    assert result.exit_code == 1
    assert "no external drafting agent configured" in result.output


# ── proposal-list ──────────────────────────────────────────────────


def test_proposal_list_reports_state_review_and_topic_count(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    proposal_id = _propose(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-list", "--repo", str(fake_git_repo), "--json",
    ])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    row = next(r for r in rows if r["id"] == proposal_id)
    assert row["state"] == "completed"
    assert row["review_state"] == "pending_review"
    assert row["topic_count"] == 1


def test_proposal_list_state_filter_excludes_other_states(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    _propose(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-list", "--state", "failed", "--repo", str(fake_git_repo), "--json",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


# ── proposal-show ──────────────────────────────────────────────────


def test_proposal_show_lists_topics_and_review_state(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    proposal_id = _propose(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-show", proposal_id, "--repo", str(fake_git_repo),
    ])
    assert result.exit_code == 0, result.output
    assert "run state: completed" in result.output
    assert "review state: pending_review" in result.output
    assert "stub-topic" in result.output
    assert "[pending]" in result.output


def test_proposal_show_includes_open_feedback_threads(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    proposal_id = _propose(fake_git_repo)
    add = runner.invoke(topics_cmd.topics_app, [
        "proposal-feedback", proposal_id, "--body", "Tighten the refs list",
        "--topic", "stub-topic", "--repo", str(fake_git_repo),
    ])
    assert add.exit_code == 0, add.output
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-show", proposal_id, "--repo", str(fake_git_repo), "--json",
    ])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    threads = body["open_feedback_threads"]
    assert len(threads) == 1
    assert threads[0]["kind"] == "comment"
    assert threads[0]["resolution_state"] == "open"
    assert threads[0]["snippet"] == "Tighten the refs list"


def test_proposal_show_unknown_id_exits_nonzero(fake_git_repo):
    _prepare_repo(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-show", "nope", "--repo", str(fake_git_repo),
    ])
    assert result.exit_code == 1
    assert "Proposal show failed" in result.output


# ── proposal-diff ──────────────────────────────────────────────────


def test_proposal_diff_reports_applyable_create(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    proposal_id = _propose(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-diff", proposal_id, "stub-topic", "--repo", str(fake_git_repo),
    ])
    assert result.exit_code == 0, result.output
    assert "strategy: create" in result.output
    assert "stub-topic" in result.output
    assert "applyable: yes" in result.output


def test_proposal_diff_json_dumps_full_result(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    proposal_id = _propose(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-diff", proposal_id, "stub-topic",
        "--repo", str(fake_git_repo), "--json",
    ])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["diff"]["strategy"] == "create"
    assert body["diff"]["proposed_topic_id"] == "stub-topic"
    assert "dropped_items" in body


def test_proposal_diff_rejects_bad_strategy(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    proposal_id = _propose(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-diff", proposal_id, "stub-topic",
        "--strategy", "bogus", "--repo", str(fake_git_repo),
    ])
    assert result.exit_code == 1
    assert "strategy must be one of" in result.output


# ── proposal-apply ─────────────────────────────────────────────────


def test_proposal_apply_commits_topic_into_graph(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    proposal_id = _propose(fake_git_repo)
    _mark_ready(fake_git_repo, proposal_id)
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-apply", proposal_id, "stub-topic", "--repo", str(fake_git_repo),
    ])
    assert result.exit_code == 0, result.output
    assert "Applied stub-topic" in result.output
    assert "snapshot" in result.output
    graph = load_graph_merged(fake_git_repo)
    assert "stub-topic" in graph["topics"]


def test_proposal_apply_requires_ready_review_state(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    proposal_id = _propose(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-apply", proposal_id, "stub-topic", "--repo", str(fake_git_repo),
    ])
    assert result.exit_code == 1
    assert "must be marked ready" in result.output


def test_proposal_apply_second_run_reports_already_applied(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    proposal_id = _propose(fake_git_repo)
    _mark_ready(fake_git_repo, proposal_id)
    first = runner.invoke(topics_cmd.topics_app, [
        "proposal-apply", proposal_id, "stub-topic", "--repo", str(fake_git_repo),
    ])
    assert first.exit_code == 0, first.output
    second = runner.invoke(topics_cmd.topics_app, [
        "proposal-apply", proposal_id, "stub-topic", "--repo", str(fake_git_repo),
    ])
    assert second.exit_code == 0, second.output
    assert "already applied" in second.output


# ── proposal-review-state ──────────────────────────────────────────


def test_proposal_review_state_sets_valid_state(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    proposal_id = _propose(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-review-state", proposal_id, "changes_requested",
        "--repo", str(fake_git_repo),
    ])
    assert result.exit_code == 0, result.output
    show = runner.invoke(topics_cmd.topics_app, [
        "proposal-show", proposal_id, "--repo", str(fake_git_repo), "--json",
    ])
    assert json.loads(show.output)["review_state"] == "changes_requested"


def test_proposal_review_state_rejects_invalid_state(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    proposal_id = _propose(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-review-state", proposal_id, "bogus", "--repo", str(fake_git_repo),
    ])
    assert result.exit_code == 1
    assert "state must be one of" in result.output


# ── proposal-feedback ──────────────────────────────────────────────


def test_proposal_feedback_add_then_list(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    proposal_id = _propose(fake_git_repo)
    add = runner.invoke(topics_cmd.topics_app, [
        "proposal-feedback", proposal_id, "--body", "Split this topic",
        "--repo", str(fake_git_repo),
    ])
    assert add.exit_code == 0, add.output
    assert "Feedback thread #" in add.output

    listed = runner.invoke(topics_cmd.topics_app, [
        "proposal-feedback", proposal_id, "--list",
        "--repo", str(fake_git_repo), "--json",
    ])
    assert listed.exit_code == 0, listed.output
    threads = json.loads(listed.output)
    assert len(threads) == 1
    assert threads[0]["kind"] == "comment"
    assert threads[0]["resolution_state"] == "open"
    assert threads[0]["created_by"] == "user"
    assert threads[0]["comments"][0]["body"] == "Split this topic"


def test_proposal_feedback_topic_anchor_lands_on_topic(
        stub_proposal_provider, external_agent_config, fake_git_repo):
    _prepare_repo(fake_git_repo)
    proposal_id = _propose(fake_git_repo)
    add = runner.invoke(topics_cmd.topics_app, [
        "proposal-feedback", proposal_id, "--body", "Refs look thin",
        "--topic", "stub-topic", "--repo", str(fake_git_repo), "--json",
    ])
    assert add.exit_code == 0, add.output
    thread = json.loads(add.output)
    assert thread["proposal_topic_id"] == "stub-topic"
    assert thread["anchor_kind"] == "proposal_summary"


def test_proposal_feedback_requires_body_xor_list(fake_git_repo):
    _prepare_repo(fake_git_repo)
    result = runner.invoke(topics_cmd.topics_app, [
        "proposal-feedback", "some-id", "--repo", str(fake_git_repo),
    ])
    assert result.exit_code != 0
