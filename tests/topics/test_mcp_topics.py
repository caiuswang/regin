"""Unit tests for the `topics` MCP server (lib/topics/mcp_server.py).

FastMCP tools are plain functions — call them directly with the
`stub_proposal_provider` + `fake_git_repo` fixtures, mirroring the
blueprint apply tests' setup (seed a Repo row, create a stubbed
proposal run, then exercise the tool surface).
"""

from __future__ import annotations

from lib.orm import SessionLocal
from lib.orm.models import Repo
from lib.topics import bootstrap, load_graph_merged
from lib.topics.mcp_server import (
    proposal_apply,
    proposal_diff,
    proposal_feedback_add,
    proposal_feedback_list,
    proposal_list,
    proposal_review_state,
    proposal_show,
)
from lib.topics.proposals import create_proposal_run, load_proposal

RUN_ID = "20260101T000000Z"


def _seed_repo(path, name="mcp-repo") -> str:
    with SessionLocal() as s:
        s.add(Repo(name=name, path=str(path), default_branch="main", is_active=1))
        s.commit()
    return name


def _make_proposal(fake_git_repo) -> tuple[str, str]:
    """Bootstrap the approved graph and create one stubbed proposal run.

    Returns (proposal_id, topic_id). Callers must activate the
    `stub_proposal_provider` fixture."""
    bootstrap(fake_git_repo)
    create_proposal_run(str(fake_git_repo), run_id=RUN_ID)
    proposal = load_proposal(str(fake_git_repo), RUN_ID)
    assert proposal["topics"], "stub drafter produced no topics"
    return RUN_ID, proposal["topics"][0]["id"]


# ── repo resolution ─────────────────────────────────────────────────


def test_unknown_repo_returns_one_line_error(tmp_db):
    out = proposal_list("no-such-repo")
    assert "unknown repo" in out
    assert "\n" not in out


def test_empty_repo_argument_is_rejected(tmp_db):
    assert "repo is required" in proposal_list("")


def test_repo_resolves_by_name_and_by_path(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, _ = _make_proposal(fake_git_repo)
    by_name = proposal_list(name)
    by_path = proposal_list(str(fake_git_repo))
    assert proposal_id in by_name
    assert by_name == by_path


# ── proposal_list ───────────────────────────────────────────────────


def test_list_shows_run_review_state_and_topic_count(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, _ = _make_proposal(fake_git_repo)
    out = proposal_list(name)
    assert proposal_id in out
    assert "run=completed" in out
    assert "review=pending_review" in out
    assert "topics=1" in out


def test_list_filters_by_run_state(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    _make_proposal(fake_git_repo)
    assert "no proposal runs in state 'failed'" in proposal_list(name, state="failed")
    assert RUN_ID in proposal_list(name, state="completed")


def test_list_with_no_runs(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    assert proposal_list(name) == "no proposal runs"


# ── proposal_show ───────────────────────────────────────────────────


def test_show_renders_topics_and_feedback(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _make_proposal(fake_git_repo)
    out = proposal_show(name, proposal_id)
    assert f"{proposal_id} · run=completed · review=pending_review" in out
    assert topic_id in out
    assert "review_status=pending" in out
    assert "open feedback: none" in out


def test_show_unknown_proposal_is_one_line_error(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    _make_proposal(fake_git_repo)
    out = proposal_show(name, "bogus-id")
    assert "not found" in out


def test_show_lists_open_feedback_threads(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _make_proposal(fake_git_repo)
    proposal_feedback_add(name, proposal_id, "tighten the intent", topic_id=topic_id)
    out = proposal_show(name, proposal_id)
    assert "open feedback (1):" in out
    assert "tighten the intent" in out


# ── proposal_diff ───────────────────────────────────────────────────


def test_diff_reports_applyable_create(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _make_proposal(fake_git_repo)
    out = proposal_diff(name, proposal_id, topic_id)
    assert "is_applyable: yes" in out
    assert f"create {topic_id}" in out
    assert "introduced_errors: none" in out


def test_diff_rejects_unknown_strategy(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _make_proposal(fake_git_repo)
    out = proposal_diff(name, proposal_id, topic_id, strategy="bogus")
    assert "strategy must be one of" in out


def test_diff_unknown_proposal_is_one_line_error(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    _make_proposal(fake_git_repo)
    out = proposal_diff(name, "bogus-id", "whatever")
    assert "proposal not found" in out


# ── proposal_apply ──────────────────────────────────────────────────


def test_apply_refuses_when_not_ready(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _make_proposal(fake_git_repo)
    out = proposal_apply(name, proposal_id, topic_id)
    assert "marked ready" in out
    assert topic_id not in load_graph_merged(fake_git_repo)["topics"]


def test_apply_success_commits_snapshot_and_graph(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _make_proposal(fake_git_repo)
    assert "ready_to_apply" in proposal_review_state(name, proposal_id, "ready_to_apply")

    out = proposal_apply(name, proposal_id, topic_id)
    assert "applied — snapshot" in out
    assert topic_id in load_graph_merged(fake_git_repo)["topics"]
    assert "review=applied" in proposal_list(name)


def test_apply_twice_reports_already_applied(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _make_proposal(fake_git_repo)
    proposal_review_state(name, proposal_id, "ready_to_apply")
    first = proposal_apply(name, proposal_id, topic_id)
    assert "applied — snapshot" in first

    second = proposal_apply(name, proposal_id, topic_id)
    assert "already applied (no-op)" in second


# ── proposal_review_state ───────────────────────────────────────────


def test_review_state_round_trip(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, _ = _make_proposal(fake_git_repo)
    out = proposal_review_state(name, proposal_id, "changes_requested")
    assert out == f"{proposal_id} review state set to changes_requested"
    assert "review=changes_requested" in proposal_list(name)


def test_review_state_rejects_invalid_state(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, _ = _make_proposal(fake_git_repo)
    assert "review_state must be one of" in proposal_review_state(name, proposal_id, "bogus")


# ── feedback add / list ─────────────────────────────────────────────


def test_feedback_add_and_list(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, topic_id = _make_proposal(fake_git_repo)
    assert f"no feedback threads on {proposal_id}" in proposal_feedback_list(name, proposal_id)

    out = proposal_feedback_add(
        name, proposal_id, "split this into two topics", topic_id=topic_id)
    assert "feedback thread #" in out
    assert f"on topic {topic_id}" in out

    listed = proposal_feedback_list(name, proposal_id)
    assert "split this into two topics" in listed
    assert "review_note" in listed
    assert f"(topic {topic_id})" in listed


def test_feedback_add_requires_body(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, _ = _make_proposal(fake_git_repo)
    assert "body is required" in proposal_feedback_add(name, proposal_id, "   ")


def test_feedback_add_rejects_unknown_topic(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    proposal_id, _ = _make_proposal(fake_git_repo)
    out = proposal_feedback_add(name, proposal_id, "note", topic_id="nope")
    assert "proposed topic not found" in out


def test_feedback_list_unknown_proposal_is_error(stub_proposal_provider, fake_git_repo):
    name = _seed_repo(fake_git_repo)
    _make_proposal(fake_git_repo)
    assert "not found" in proposal_feedback_list(name, "bogus-id")
