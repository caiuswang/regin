"""Workflow tests for the proposal state machine.

Companion to test_topic_proposals.py — that file covers operation-level
behavior, this file covers the state-transition matrix.

State machine summary:

  Proposal run-level status (proposal["status"] / metadata.proposal_status):

      draft → pending_review → ready_to_apply → applied
              ↓                ↓                ↑
              changes_requested ↑                partially_applied
                                regenerate → pending_review (markers cleared)

  Topic-level review_status:
      null (pending) → accepted | merged | ignored

Invariant: proposal.status follows topic review_status counts:
  - every topic reviewed → applied
  - some topics reviewed → partially_applied
  - no topics reviewed → preserves draft/pending_review/changes_requested/ready_to_apply

The library-level accept/replace/merge/ignore functions and the web /apply
endpoint MUST converge on this invariant. Divergence here is the bug class
this file regresses against (see lib/topics/proposals.py:_recompute_proposal_status).
"""

from __future__ import annotations

import json
import subprocess

import pytest

from lib.topics import (
    TopicGraphError, bootstrap, load_graph, load_graph_merged,
    save_graph, utc_now,
)
from lib.topics.proposals import (
    accept_proposed_topic,
    create_proposal_run,
    downgrade_topic_to_proposal,
    ignore_proposed_topic,
    load_proposal,
    merge_proposed_topic,
    regenerate_proposal_run,
    replace_approved_topic,
    restore_proposal_to_revision,
    save_proposal,
    set_proposal_review_state,
)


# ── helpers ──────────────────────────────────────────────────────────


def _ensure_bootstrap(repo):
    """Bootstrap the topic graph idempotently — bootstrap() raises if already done."""
    from lib.topics import topic_path
    if not topic_path(repo).exists():
        bootstrap(repo)


def _seed_two_topic_proposal(repo, run_id="run1"):
    """Plant a proposal with two pending topics so partial-vs-all logic is testable."""
    _ensure_bootstrap(repo)
    create_proposal_run(repo, run_id=run_id)
    proposal = load_proposal(repo, run_id)
    proposal["topics"] = [
        {
            "id": "alpha",
            "label": "Alpha",
            "aliases": [],
            "intent": "Alpha topic.",
            "status": "active",
            "refs": [],
            "edges": [],
            "commands": [],
            "include_globs": [],
            "exclude_globs": [],
            "evidence_paths": [],
        },
        {
            "id": "beta",
            "label": "Beta",
            "aliases": [],
            "intent": "Beta topic.",
            "status": "active",
            "refs": [],
            "edges": [],
            "commands": [],
            "include_globs": [],
            "exclude_globs": [],
            "evidence_paths": [],
        },
    ]
    save_proposal(repo, run_id, proposal)
    return proposal


def _seed_single_topic_proposal(repo, run_id="run1", topic_id="alpha"):
    _ensure_bootstrap(repo)
    create_proposal_run(repo, run_id=run_id)
    proposal = load_proposal(repo, run_id)
    proposal["topics"] = [
        {
            "id": topic_id,
            "label": topic_id.capitalize(),
            "aliases": [],
            "intent": f"{topic_id} topic.",
            "status": "active",
            "refs": [],
            "edges": [],
            "commands": [],
            "include_globs": [],
            "exclude_globs": [],
            "evidence_paths": [],
        }
    ]
    save_proposal(repo, run_id, proposal)
    return proposal


def _seed_repo_record(repo):
    from lib.orm import SessionLocal
    from lib.orm.models import Repo
    from sqlmodel import select
    with SessionLocal() as s:
        existing = s.exec(select(Repo).where(Repo.path == str(repo))).first()
        if existing:
            return existing.name
        rec = Repo(name=repo.name, path=str(repo))
        s.add(rec)
        s.commit()
        return rec.name


# ── Initial states ───────────────────────────────────────────────────


def test_create_proposal_initial_status_is_draft_or_pending_review(stub_proposal_provider, fake_git_repo):
    bootstrap(fake_git_repo)
    create_proposal_run(fake_git_repo, run_id="run1")
    proposal = load_proposal(fake_git_repo, "run1")
    # Provider sets initial status — stub uses "draft". Either draft or
    # pending_review is acceptable as long as no topic is yet reviewed.
    assert proposal["status"] in {"draft", "pending_review"}
    assert all(not t.get("review_status") for t in proposal["topics"])


def test_downgrade_proposal_initial_status_is_draft(stub_proposal_provider, fake_git_repo):
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["service"] = {
        "label": "Service", "aliases": [], "intent": "Service.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    result = downgrade_topic_to_proposal(fake_git_repo, "service")
    proposal = load_proposal(fake_git_repo, result["id"])
    assert proposal["status"] == "draft"


# ── set_proposal_review_state ────────────────────────────────────────


def test_mark_ready_from_pending_review(stub_proposal_provider, fake_git_repo):
    _seed_single_topic_proposal(fake_git_repo)
    set_proposal_review_state(fake_git_repo, "run1", "pending_review")
    set_proposal_review_state(fake_git_repo, "run1", "ready_to_apply")
    assert load_proposal(fake_git_repo, "run1")["status"] == "ready_to_apply"


def test_mark_changes_requested_from_pending_review(stub_proposal_provider, fake_git_repo):
    _seed_single_topic_proposal(fake_git_repo)
    set_proposal_review_state(fake_git_repo, "run1", "changes_requested")
    assert load_proposal(fake_git_repo, "run1")["status"] == "changes_requested"


def test_set_review_state_rejects_invalid_state(stub_proposal_provider, fake_git_repo):
    _seed_single_topic_proposal(fake_git_repo)
    with pytest.raises(TopicGraphError):
        set_proposal_review_state(fake_git_repo, "run1", "bogus_state")


# ── Library accept (covers bug: lib funcs didn't sync proposal.status) ─


def test_accept_single_topic_sets_applied(stub_proposal_provider, fake_git_repo):
    _seed_single_topic_proposal(fake_git_repo, topic_id="alpha")
    accept_proposed_topic(fake_git_repo, "run1", "alpha")
    saved = load_proposal(fake_git_repo, "run1")
    assert saved["topics"][0]["review_status"] == "accepted"
    assert saved["status"] == "applied"


def test_accept_one_of_many_sets_partially_applied(stub_proposal_provider, fake_git_repo):
    _seed_two_topic_proposal(fake_git_repo)
    accept_proposed_topic(fake_git_repo, "run1", "alpha")
    saved = load_proposal(fake_git_repo, "run1")
    by_id = {t["id"]: t for t in saved["topics"]}
    assert by_id["alpha"]["review_status"] == "accepted"
    assert not by_id["beta"].get("review_status")
    assert saved["status"] == "partially_applied"


def test_accept_last_pending_topic_sets_applied(stub_proposal_provider, fake_git_repo):
    _seed_two_topic_proposal(fake_git_repo)
    accept_proposed_topic(fake_git_repo, "run1", "alpha")
    accept_proposed_topic(fake_git_repo, "run1", "beta")
    saved = load_proposal(fake_git_repo, "run1")
    assert saved["status"] == "applied"


# ── Library replace (covers same bug) ────────────────────────────────


def test_replace_topic_recomputes_status(stub_proposal_provider, fake_git_repo):
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["alpha"] = {
        "label": "Alpha (existing)", "aliases": [], "intent": "old.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)
    _seed_single_topic_proposal(fake_git_repo, topic_id="alpha")

    replace_approved_topic(fake_git_repo, "run1", "alpha")
    saved = load_proposal(fake_git_repo, "run1")
    assert saved["topics"][0]["review_status"] == "accepted"
    assert saved["topics"][0].get("replaced_existing") is True
    assert saved["status"] == "applied"


# ── Library merge (covers same bug) ──────────────────────────────────


def _seed_merge_target_topic(repo, topic_id="target"):
    graph = load_graph(repo)
    graph["topics"][topic_id] = {
        "label": "Target", "aliases": [], "intent": "Target topic.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    save_graph(repo, graph)


def test_merge_single_topic_sets_applied(stub_proposal_provider, fake_git_repo):
    _seed_single_topic_proposal(fake_git_repo, topic_id="alpha")
    _seed_merge_target_topic(fake_git_repo)
    merge_proposed_topic(fake_git_repo, "run1", "alpha", "target")
    saved = load_proposal(fake_git_repo, "run1")
    assert saved["topics"][0]["review_status"] == "merged"
    assert saved["status"] == "applied"


def test_merge_one_of_many_sets_partially_applied(stub_proposal_provider, fake_git_repo):
    _seed_two_topic_proposal(fake_git_repo)
    _seed_merge_target_topic(fake_git_repo)
    merge_proposed_topic(fake_git_repo, "run1", "alpha", "target")
    saved = load_proposal(fake_git_repo, "run1")
    assert saved["status"] == "partially_applied"


# ── Ignore (covers bug: partially_applied wasn't set) ────────────────


def test_ignore_single_topic_sets_applied(stub_proposal_provider, fake_git_repo):
    _seed_single_topic_proposal(fake_git_repo)
    ignore_proposed_topic(fake_git_repo, "run1", "alpha")
    saved = load_proposal(fake_git_repo, "run1")
    assert saved["topics"][0]["review_status"] == "ignored"
    assert saved["status"] == "applied"


def test_ignore_one_of_many_sets_partially_applied(stub_proposal_provider, fake_git_repo):
    _seed_two_topic_proposal(fake_git_repo)
    ignore_proposed_topic(fake_git_repo, "run1", "alpha")
    saved = load_proposal(fake_git_repo, "run1")
    assert saved["status"] == "partially_applied"


def test_ignore_mixed_with_accept_progresses_status(stub_proposal_provider, fake_git_repo):
    _seed_two_topic_proposal(fake_git_repo)
    accept_proposed_topic(fake_git_repo, "run1", "alpha")
    assert load_proposal(fake_git_repo, "run1")["status"] == "partially_applied"
    ignore_proposed_topic(fake_git_repo, "run1", "beta")
    assert load_proposal(fake_git_repo, "run1")["status"] == "applied"


# ── Web /apply ↔ lib accept parity ───────────────────────────────────


def test_web_apply_and_lib_accept_yield_identical_proposal_status(
    stub_proposal_provider, flask_client, fake_git_repo, tmp_db
):
    """Regression invariant: the web /apply endpoint and the lib
    accept_proposed_topic must produce the same proposal.status. Before
    the bug-fix pass these diverged — /apply set "applied" while the lib
    function left whatever was there (draft/pending_review)."""
    name = _seed_repo_record(fake_git_repo)
    _seed_single_topic_proposal(fake_git_repo, run_id="web-run", topic_id="alpha")
    _seed_single_topic_proposal(fake_git_repo, run_id="lib-run", topic_id="beta")
    # /apply requires the proposal to be marked ready first.
    set_proposal_review_state(fake_git_repo, "web-run", "ready_to_apply")

    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/web-run/topics/alpha/apply",
        json={"strategy": "create"},
    )
    assert resp.status_code == 200, resp.get_json()

    accept_proposed_topic(fake_git_repo, "lib-run", "beta")

    web_status = load_proposal(fake_git_repo, "web-run")["status"]
    lib_status = load_proposal(fake_git_repo, "lib-run")["status"]
    assert web_status == lib_status == "applied"


# ── Regenerate (verify earlier session fixes still hold) ─────────────


def test_regenerate_from_applied_resets_to_pending_review(stub_proposal_provider, fake_git_repo):
    _seed_single_topic_proposal(fake_git_repo, topic_id="alpha")
    accept_proposed_topic(fake_git_repo, "run1", "alpha")
    assert load_proposal(fake_git_repo, "run1")["status"] == "applied"

    regenerate_proposal_run(fake_git_repo, "run1")

    saved = load_proposal(fake_git_repo, "run1")
    assert saved["status"] == "pending_review"
    # Marker cleared by _reset_review_markers_for_regenerate.
    assert not saved["topics"][0].get("review_status")


def test_regenerate_from_partially_applied_resets_to_pending_review(stub_proposal_provider, fake_git_repo):
    _seed_two_topic_proposal(fake_git_repo)
    accept_proposed_topic(fake_git_repo, "run1", "alpha")
    assert load_proposal(fake_git_repo, "run1")["status"] == "partially_applied"

    regenerate_proposal_run(fake_git_repo, "run1")

    saved = load_proposal(fake_git_repo, "run1")
    assert saved["status"] == "pending_review"
    assert all(not t.get("review_status") for t in saved["topics"])


# ── Downgrade (full lifecycle) ───────────────────────────────────────


def test_downgrade_creates_proposal_with_topic_review_status_pending(stub_proposal_provider, fake_git_repo):
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["alpha"] = {
        "label": "Alpha", "aliases": [], "intent": "Alpha.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    result = downgrade_topic_to_proposal(fake_git_repo, "alpha")
    proposal = load_proposal(fake_git_repo, result["id"])
    topic = proposal["topics"][0]
    assert topic["review_status"] == "pending"
    assert topic.get("downgraded_from") == "alpha"
    assert topic.get("downgraded_at")
    assert topic.get("accepted_topic") is None
    assert topic.get("accepted_at") is None


def test_downgrade_removes_topic_from_approved_graph(stub_proposal_provider, fake_git_repo):
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["alpha"] = {
        "label": "Alpha", "aliases": [], "intent": "Alpha.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    downgrade_topic_to_proposal(fake_git_repo, "alpha")

    assert "alpha" not in load_graph_merged(fake_git_repo)["topics"]


def test_downgrade_appends_revision_to_origin_proposal(
    stub_proposal_provider, fake_git_repo
):
    """When topic_audits remembers the proposal that brought a topic
    into the approved graph, downgrade must append a new revision on
    that origin run instead of spawning a fresh timestamp-id proposal.
    """
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    _seed_single_topic_proposal(fake_git_repo, topic_id="alpha")
    accept_proposed_topic(fake_git_repo, "run1", "alpha")
    before = load_proposal(fake_git_repo, "run1")
    assert before["revision"]["revision_number"] == 1

    result = downgrade_topic_to_proposal(fake_git_repo, "alpha")

    assert result["id"] == "run1"
    assert result.get("merged_into_origin") is True
    after = load_proposal(fake_git_repo, "run1")
    assert after["revision"]["revision_number"] == 2
    assert after["revision"]["kind"] == "downgraded"
    assert after["revision"]["metadata"]["downgrade_origin"] is True
    assert after["revision"]["metadata"]["downgraded_from_topic_id"] == "alpha"
    assert after["status"] == "changes_requested"


def test_downgrade_falls_back_to_fresh_proposal_when_origin_deleted(
    stub_proposal_provider, fake_git_repo
):
    """If the proposal that originally applied the topic has since been
    deleted from the runs table, the origin lookup must report None and
    the legacy fresh-proposal path takes over so the user still gets a
    draft to work with.
    """
    from lib.topics.proposals import delete_proposal_run

    _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    _seed_single_topic_proposal(fake_git_repo, topic_id="alpha")
    accept_proposed_topic(fake_git_repo, "run1", "alpha")
    delete_proposal_run(fake_git_repo, "run1")

    result = downgrade_topic_to_proposal(fake_git_repo, "alpha")
    assert result["id"] != "run1"
    # Legacy path returns a fresh timestamp id with merged_into_origin absent.
    assert "merged_into_origin" not in result


def test_downgrade_prunes_inbound_edges_from_sibling_topics(
    stub_proposal_provider, fake_git_repo
):
    """If another approved topic has an edge pointing at the topic being
    downgraded, validate() would fail with edge_target_missing and roll
    back. The downgrade must prune those inbound edges itself.
    """
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["alpha"] = {
        "label": "Alpha", "aliases": [], "intent": "Alpha.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    graph["topics"]["beta"] = {
        "label": "Beta", "aliases": [], "intent": "Beta.",
        "status": "active", "refs": [],
        "edges": [{"target": "alpha", "type": "related"}],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    downgrade_topic_to_proposal(fake_git_repo, "alpha")

    after = load_graph_merged(fake_git_repo)
    assert "alpha" not in after["topics"]
    assert after["topics"]["beta"]["edges"] == []


def test_apply_restores_pruned_inbound_edges_round_trip(
    stub_proposal_provider, fake_git_repo, flask_client
):
    """End-to-end: downgrade alpha (prunes beta → alpha edge), then
    re-apply alpha via the web endpoint. The edge on beta must come
    back automatically.
    """
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["alpha"] = {
        "label": "Alpha", "aliases": [], "intent": "Alpha.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    graph["topics"]["beta"] = {
        "label": "Beta", "aliases": [], "intent": "Beta.",
        "status": "active", "refs": [],
        "edges": [{"target": "alpha", "type": "related"}],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    result = downgrade_topic_to_proposal(fake_git_repo, "alpha")
    proposal_id = result["id"]
    assert load_graph_merged(fake_git_repo)["topics"]["beta"]["edges"] == []

    set_proposal_review_state(fake_git_repo, proposal_id, "ready_to_apply")
    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/alpha/apply",
        json={"strategy": "create"},
    )
    assert resp.status_code == 200, resp.get_json()

    after = load_graph_merged(fake_git_repo)
    assert "alpha" in after["topics"]
    assert after["topics"]["beta"]["edges"] == [{"target": "alpha", "type": "related"}]


def test_downgrade_records_pruned_edges_in_proposal_metadata(
    stub_proposal_provider, fake_git_repo
):
    """The pruned inbound edges must land on proposal metadata so the
    apply path can restore them on re-approval.
    """
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["alpha"] = {
        "label": "Alpha", "aliases": [], "intent": "Alpha.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    graph["topics"]["beta"] = {
        "label": "Beta", "aliases": [], "intent": "Beta.",
        "status": "active", "refs": [],
        "edges": [{"target": "alpha", "type": "related"}],
        "commands": [], "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    result = downgrade_topic_to_proposal(fake_git_repo, "alpha")
    proposal = load_proposal(fake_git_repo, result["id"])
    bucket = (proposal.get("metadata") or {}).get("pruned_inbound_edges") or {}
    assert "alpha" in bucket
    assert bucket["alpha"]["beta"] == [{"target": "alpha", "type": "related"}]


def test_downgrade_triggers_wiki_pattern_reconcile(
    stub_proposal_provider, fake_git_repo, monkeypatch
):
    """The downgraded topic's wiki/<repo>/<id> PatternDoc row must be
    swept by the wiki indexer — otherwise it lingers in the repo's
    /api/repos/<name> patterns table after the topic is no longer
    approved. We can't easily seed an indexer in the test sandbox, so
    we just verify _reindex_wiki_after_graph_change fires.
    """
    from lib.topics import proposals as proposals_mod

    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["alpha"] = {
        "label": "Alpha", "aliases": [], "intent": "Alpha.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    calls = []
    from lib.topics.proposals import downgrade as downgrade_mod
    monkeypatch.setattr(
        downgrade_mod, "_reindex_wiki_after_graph_change",
        lambda repo_path: calls.append(str(repo_path)),
    )

    downgrade_topic_to_proposal(fake_git_repo, "alpha")
    assert calls == [str(fake_git_repo)]


def test_downgrade_clears_accept_marker_on_other_proposals(stub_proposal_provider, fake_git_repo):
    """orm_unaccept_topic_across_proposals invariant: when a topic is
    downgraded, every proposal that previously accepted it must shed
    its review_status='accepted' so the original draft becomes
    re-acceptable.
    """
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("svc")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "svc"])
    _seed_single_topic_proposal(fake_git_repo, run_id="src-run", topic_id="service")
    accept_proposed_topic(fake_git_repo, "src-run", "service")
    assert load_proposal(fake_git_repo, "src-run")["topics"][0]["review_status"] == "accepted"

    downgrade_topic_to_proposal(fake_git_repo, "service")

    src = load_proposal(fake_git_repo, "src-run")
    topic = src["topics"][0]
    assert topic.get("review_status") in (None, "pending")
    assert topic.get("accepted_topic") is None
    assert topic.get("accepted_at") is None


def test_downgrade_flips_source_proposal_status_to_changes_requested(
    stub_proposal_provider, fake_git_repo
):
    """Under the merge-into-origin design, downgrading a topic appends a
    new revision (kind='downgraded') to the proposal that originally
    applied it, and resets the run status to `changes_requested` so the
    proposal surfaces as actionable again. Previously this test guarded
    the opposite invariant — leaving status='applied' — when downgrade
    spawned a fresh proposal that didn't touch the origin.
    """
    (fake_git_repo / "service").mkdir()
    (fake_git_repo / "service" / "api.py").write_text("svc")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "svc"])
    _seed_single_topic_proposal(fake_git_repo, run_id="src-run", topic_id="service")
    accept_proposed_topic(fake_git_repo, "src-run", "service")
    assert load_proposal(fake_git_repo, "src-run")["status"] == "applied"

    downgrade_topic_to_proposal(fake_git_repo, "service")

    src = load_proposal(fake_git_repo, "src-run")
    assert src["topics"][0].get("review_status") in (None, "pending")
    assert src["status"] == "changes_requested"
    assert src["revision"]["kind"] == "downgraded"


def test_downgrade_carries_approved_topic_wiki_into_new_proposal(stub_proposal_provider, fake_git_repo):
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["alpha"] = {
        "label": "Alpha", "aliases": [], "intent": "Alpha.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)
    wiki_dir = fake_git_repo / ".regin" / "topics" / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "alpha.md").write_text("# Alpha custom wiki\n\nHand-authored body.\n")

    result = downgrade_topic_to_proposal(fake_git_repo, "alpha")

    proposal = load_proposal(fake_git_repo, result["id"])
    wiki_text = proposal.get("wiki") or ""
    assert "Alpha custom wiki" in wiki_text


def test_downgrade_then_mark_ready_then_apply_round_trip(
    stub_proposal_provider, flask_client, fake_git_repo, tmp_db
):
    """In-graph → downgrade → mark ready → apply → back in graph."""
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["alpha"] = {
        "label": "Alpha", "aliases": [], "intent": "Alpha.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    result = downgrade_topic_to_proposal(fake_git_repo, "alpha")
    proposal_id = result["id"]
    assert "alpha" not in load_graph_merged(fake_git_repo)["topics"]
    assert load_proposal(fake_git_repo, proposal_id)["status"] == "draft"

    set_proposal_review_state(fake_git_repo, proposal_id, "ready_to_apply")
    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/alpha/apply",
        json={"strategy": "create"},
    )
    assert resp.status_code == 200, resp.get_json()

    assert "alpha" in load_graph_merged(fake_git_repo)["topics"]
    assert load_proposal(fake_git_repo, proposal_id)["status"] == "applied"


def test_downgrade_nonexistent_topic_raises(stub_proposal_provider, fake_git_repo):
    bootstrap(fake_git_repo)
    with pytest.raises(TopicGraphError, match="not found"):
        downgrade_topic_to_proposal(fake_git_repo, "ghost-topic")


def test_regenerate_downgraded_proposal_clears_stale_accept_marker(
    stub_proposal_provider, fake_git_repo
):
    """Reproduces user's `20260520T104446Z` scenario: a downgrade proposal
    whose topic was re-accepted (review_status='accepted') and then the
    user wants a fresh draft. Regenerate must clear the stale marker so
    Mark Ready / Apply work again on the new revision.
    """
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["alpha"] = {
        "label": "Alpha", "aliases": [], "intent": "Alpha.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)
    result = downgrade_topic_to_proposal(fake_git_repo, "alpha")
    proposal_id = result["id"]
    # Simulate the user re-accepting the downgraded draft (sets
    # review_status='accepted' and bumps proposal.status='applied').
    accept_proposed_topic(fake_git_repo, proposal_id, "alpha")
    accepted_topic = load_proposal(fake_git_repo, proposal_id)["topics"][0]
    assert accepted_topic["review_status"] == "accepted"

    regenerate_proposal_run(fake_git_repo, proposal_id)

    saved = load_proposal(fake_git_repo, proposal_id)
    assert saved["status"] == "pending_review"
    topic = saved["topics"][0]
    assert not topic.get("review_status")
    assert not topic.get("accepted_topic")
    assert not topic.get("accepted_at")


def test_apply_downgraded_proposal_reinstates_topic_in_graph(
    stub_proposal_provider, flask_client, fake_git_repo, tmp_db
):
    name = _seed_repo_record(fake_git_repo)
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["alpha"] = {
        "label": "Alpha", "aliases": [], "intent": "Alpha.",
        "status": "active", "refs": [], "edges": [], "commands": [],
        "include_globs": [], "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)
    result = downgrade_topic_to_proposal(fake_git_repo, "alpha")
    proposal_id = result["id"]
    assert "alpha" not in load_graph_merged(fake_git_repo)["topics"]

    set_proposal_review_state(fake_git_repo, proposal_id, "ready_to_apply")
    resp = flask_client.post(
        f"/api/repos/{name}/topics/proposals/{proposal_id}/topics/alpha/apply",
        json={"strategy": "create"},
    )
    assert resp.status_code == 200, resp.get_json()

    assert "alpha" in load_graph_merged(fake_git_repo)["topics"]


# ── State-consistency invariants ─────────────────────────────────────


def test_unreviewed_proposal_never_flagged_applied(stub_proposal_provider, fake_git_repo):
    """If no topic has been reviewed, proposal.status must never be applied/partially_applied."""
    _seed_two_topic_proposal(fake_git_repo)
    saved = load_proposal(fake_git_repo, "run1")
    assert saved["status"] not in {"applied", "partially_applied"}


@pytest.mark.parametrize("operation", ["accept", "merge", "ignore"])
def test_fully_reviewed_proposal_flagged_applied_regardless_of_path(
    stub_proposal_provider, fake_git_repo, operation
):
    """Whichever lib operation finishes the last unreviewed topic must end at 'applied'."""
    _seed_two_topic_proposal(fake_git_repo)
    if operation == "merge":
        _seed_merge_target_topic(fake_git_repo)
    # Always accept the first; vary how we close out the second.
    accept_proposed_topic(fake_git_repo, "run1", "alpha")
    if operation == "accept":
        accept_proposed_topic(fake_git_repo, "run1", "beta")
    elif operation == "merge":
        merge_proposed_topic(fake_git_repo, "run1", "beta", "target")
    elif operation == "ignore":
        ignore_proposed_topic(fake_git_repo, "run1", "beta")
    assert load_proposal(fake_git_repo, "run1")["status"] == "applied"


# ── Restore to historical revision ───────────────────────────────────


def _seed_two_revision_run(repo, *, r1_wiki="# r1 wiki body", r2_wiki="# r2 wiki body"):
    """Plant a run with r1 (topic alpha) + r2 (topic beta, regenerated). Returns (r1, r2)."""
    from lib.topics import topic_dir
    from lib.topics.proposal_orm import orm_save_proposal

    proposal = _seed_single_topic_proposal(repo, topic_id="alpha")
    # save_proposal only forwards `proposal["wiki"]` on revision *create*; r1
    # already exists, so go straight to orm_save_proposal with wiki=.
    orm_save_proposal(repo, "run1", proposal, wiki=r1_wiki)
    # The regenerate path in production writes wiki.md to disk too —
    # mirror that here so the seed matches what restore actually sees.
    wiki_disk_path = topic_dir(repo) / "proposals" / "run1" / "wiki.md"
    wiki_disk_path.write_text(r1_wiki)
    r1 = load_proposal(repo, "run1")

    r2_payload = {
        **r1,
        "wiki": r2_wiki,
        "topics": [{
            "id": "beta", "label": "Beta", "aliases": [], "intent": "Beta topic.",
            "status": "active", "refs": [], "edges": [], "commands": [],
            "include_globs": [], "exclude_globs": [], "evidence_paths": [],
        }],
        "status": "ready_to_apply",
    }
    orm_save_proposal(
        repo, "run1", r2_payload,
        wiki=r2_wiki, append_revision=True, revision_kind="regenerated",
    )
    wiki_disk_path.write_text(r2_wiki)
    return r1, load_proposal(repo, "run1")


def test_restore_revision_creates_third_revision(stub_proposal_provider, fake_git_repo):
    """Restoring r1 appends r3 as the new latest (kind='restored')."""
    r1, r2 = _seed_two_revision_run(fake_git_repo)
    restored = restore_proposal_to_revision(fake_git_repo, "run1", r1["revision"]["id"])
    rev = restored["revision"]
    assert rev["revision_number"] == 3
    assert rev["kind"] == "restored"
    assert rev["is_latest"] is True
    assert rev["parent_revision_id"] == r2["revision"]["id"]


def test_restore_revision_records_source_in_metadata(stub_proposal_provider, fake_git_repo):
    """The new revision's metadata points back at the source revision."""
    r1, _ = _seed_two_revision_run(fake_git_repo)
    restored = restore_proposal_to_revision(fake_git_repo, "run1", r1["revision"]["id"])
    meta = restored["revision"]["metadata"]
    assert meta["restored_from_revision_id"] == r1["revision"]["id"]
    assert meta["restored_from_revision_number"] == 1


def test_restore_revision_copies_source_content(stub_proposal_provider, fake_git_repo):
    """Wiki + topics on the new revision match the source revision."""
    r1, _ = _seed_two_revision_run(fake_git_repo)
    restored = restore_proposal_to_revision(fake_git_repo, "run1", r1["revision"]["id"])
    assert restored["wiki"] == "# r1 wiki body"
    assert [t["id"] for t in restored["topics"]] == ["alpha"]


def test_restore_revision_resets_run_status_to_pending_review(
    stub_proposal_provider, fake_git_repo
):
    """Run-level status resets so the Mark ready → Apply flow works on r3."""
    r1, _ = _seed_two_revision_run(fake_git_repo)
    restored = restore_proposal_to_revision(fake_git_repo, "run1", r1["revision"]["id"])
    assert restored["status"] == "pending_review"


def test_restore_revision_preserves_historical_revisions(
    stub_proposal_provider, fake_git_repo
):
    """r1 and r2 stay in the revision list — restore is additive, not destructive."""
    r1, _ = _seed_two_revision_run(fake_git_repo)
    restored = restore_proposal_to_revision(fake_git_repo, "run1", r1["revision"]["id"])
    numbers = sorted(r["revision_number"] for r in restored["revisions"])
    assert numbers == [1, 2, 3]


def test_restore_revision_strips_stale_accept_markers(
    stub_proposal_provider, fake_git_repo
):
    """If the source revision's topics were marked accepted long ago, the
    restored copy must come back as `pending` so the Accept button is
    visible again.
    """
    from lib.topics.proposal_orm import orm_save_proposal

    _seed_single_topic_proposal(fake_git_repo, topic_id="alpha")
    accept_proposed_topic(fake_git_repo, "run1", "alpha")
    r1 = load_proposal(fake_git_repo, "run1")
    assert r1["topics"][0]["review_status"] == "accepted"
    r1_revision_id = r1["revision"]["id"]

    # Append a regenerated revision so r1 becomes historical.
    r2_payload = {**r1, "wiki": "# r2", "status": "pending_review"}
    orm_save_proposal(
        fake_git_repo, "run1", r2_payload,
        wiki="# r2",
        append_revision=True,
        revision_kind="regenerated",
    )

    restored = restore_proposal_to_revision(fake_git_repo, "run1", r1_revision_id)
    assert all(not t.get("review_status") for t in restored["topics"])
    assert all(not t.get("accepted_topic") for t in restored["topics"])


def test_restore_revision_rejects_current_latest(stub_proposal_provider, fake_git_repo):
    """Asking to restore the already-latest revision is a no-op error."""
    _seed_single_topic_proposal(fake_git_repo)
    proposal = load_proposal(fake_git_repo, "run1")
    latest_id = proposal["revision"]["id"]
    with pytest.raises(TopicGraphError, match="already the latest"):
        restore_proposal_to_revision(fake_git_repo, "run1", latest_id)


def test_restore_revision_rejects_unknown_revision(stub_proposal_provider, fake_git_repo):
    """A revision id that doesn't exist on this run raises a clear error."""
    _seed_single_topic_proposal(fake_git_repo)
    with pytest.raises(TopicGraphError, match="not found"):
        restore_proposal_to_revision(fake_git_repo, "run1", 9999999)


def test_restore_revision_kind_survives_subsequent_save(
    stub_proposal_provider, fake_git_repo
):
    """A downstream save_proposal call (mark ready, accept, etc.) must not
    rewrite the revision's kind back to 'generated'. Before the fix,
    orm_save_proposal's revision_kind default of 'generated' silently
    clobbered the kind on every in-place update, erasing the restore
    origin in the UI.
    """
    r1, _ = _seed_two_revision_run(fake_git_repo)
    restore_proposal_to_revision(fake_git_repo, "run1", r1["revision"]["id"])

    # Simulate a downstream write that doesn't intend to change the kind.
    set_proposal_review_state(fake_git_repo, "run1", "ready_to_apply")

    after = load_proposal(fake_git_repo, "run1")
    assert after["revision"]["kind"] == "restored"
    assert after["revision"]["metadata"]["restored_from_revision_number"] == 1


def test_restore_revision_rewrites_proposal_wiki_on_disk(
    stub_proposal_provider, fake_git_repo
):
    """The apply path reads from proposals/<id>/wiki.md on disk, so the
    restore must rewrite that file — otherwise a subsequent Apply would
    publish the previous revision's wiki to the approved topic.
    """
    from lib.topics import topic_dir

    r1, _ = _seed_two_revision_run(fake_git_repo)
    wiki_path = topic_dir(fake_git_repo) / "proposals" / "run1" / "wiki.md"
    # Sanity: r2 won the disk wiki because it was written last.
    assert wiki_path.exists()
    assert "r2 wiki body" in wiki_path.read_text()

    restore_proposal_to_revision(fake_git_repo, "run1", r1["revision"]["id"])
    assert wiki_path.read_text() == "# r1 wiki body"
