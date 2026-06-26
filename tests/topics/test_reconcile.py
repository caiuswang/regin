"""Reconcile stale proposal acceptance markers against the approved graph.

Covers `lib/topics/reconcile.py`: when a topic that a proposal accepted is
later dropped from the graph *outside* the downgrade path, its
`review_status='accepted'` marker becomes a ghost. The detector must find it,
the fix must clear it (reusing the existing recovery primitive), and both must
be existence-only and idempotent.
"""

from __future__ import annotations

import pytest

from lib.topics import bootstrap, load_graph, save_graph
from lib.topics.core import load_graph_merged, load_local_graph, save_local_graph
from lib.topics.proposals import accept_proposed_topic
from lib.topics.reconcile import (
    ACCEPTED_ABSENT_CODE,
    RECONCILE_KIND,
    find_stale_acceptances,
    fix_stale_acceptances,
    record_reconcile_audit,
)


def _seed_repo_record(repo):
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models import Repo

    with SessionLocal() as s:
        existing = s.exec(select(Repo).where(Repo.path == str(repo))).first()
        if existing:
            return existing
        rec = Repo(name=repo.name, path=str(repo))
        s.add(rec)
        s.commit()
        s.refresh(rec)
        return rec


def _accept_one(repo):
    """Bootstrap, draft a single-topic proposal via the stub provider, and
    accept it — leaving `stub-topic` both in the graph and marked accepted."""
    bootstrap(repo)
    from lib.topics.proposals import create_proposal_run

    create_proposal_run(repo, run_id="run1")
    accept_proposed_topic(repo, "run1", "stub-topic")


def _drop_from_graph(repo, topic_id):
    """Delete a topic straight out of the *merged* graph (no downgrade) — the
    exact move that strands the acceptance marker. An agent-drafted accept
    lands the node in the local overlay, so sweep both layers."""
    base = load_graph(repo)
    base["topics"].pop(topic_id, None)
    save_graph(repo, base)
    overlay = load_local_graph(repo)
    overlay.get("topics", {}).pop(topic_id, None)
    save_local_graph(repo, overlay)


def test_no_stale_when_accepted_topic_present(stub_proposal_provider, fake_git_repo):
    _seed_repo_record(fake_git_repo)
    _accept_one(fake_git_repo)
    # The accepted topic still lives in the graph → nothing is stale.
    assert find_stale_acceptances(fake_git_repo) == []


def test_content_drift_alone_is_not_flagged(stub_proposal_provider, fake_git_repo):
    _seed_repo_record(fake_git_repo)
    _accept_one(fake_git_repo)
    # Mutate the live node's content (refs) but keep it present — the kind of
    # divergence `topics scan` legitimately produces. Existence-only detector
    # must NOT treat this as staleness. The accepted node lives in the overlay.
    overlay = load_local_graph(fake_git_repo)
    overlay["topics"]["stub-topic"]["refs"] = [{"path": "README.md", "role": "doc"}]
    save_local_graph(fake_git_repo, overlay)
    assert "stub-topic" in load_graph_merged(fake_git_repo)["topics"]
    assert find_stale_acceptances(fake_git_repo) == []


def test_detects_accepted_topic_absent_from_graph(stub_proposal_provider, fake_git_repo):
    _seed_repo_record(fake_git_repo)
    _accept_one(fake_git_repo)
    _drop_from_graph(fake_git_repo, "stub-topic")

    stale = find_stale_acceptances(fake_git_repo)
    assert [s["topic_id"] for s in stale] == ["stub-topic"]
    assert "run1" in stale[0]["runs"]


def test_promoted_topic_in_base_is_not_flagged(stub_proposal_provider, fake_git_repo):
    """After `promote`, the accepted node moves from the overlay into base
    `topic.json`. The detector reads the merged graph, so a base-layer topic
    must also count as present — promotion is not staleness."""
    _seed_repo_record(fake_git_repo)
    _accept_one(fake_git_repo)
    from lib.topics import promote_all_topics

    promote_all_topics(fake_git_repo)
    assert "stub-topic" in load_graph(fake_git_repo)["topics"]
    assert find_stale_acceptances(fake_git_repo) == []


def test_fix_clears_markers_and_is_idempotent(stub_proposal_provider, fake_git_repo):
    _seed_repo_record(fake_git_repo)
    _accept_one(fake_git_repo)
    _drop_from_graph(fake_git_repo, "stub-topic")

    stale = find_stale_acceptances(fake_git_repo)
    assert stale, "precondition: a stale acceptance exists"

    reset = fix_stale_acceptances(fake_git_repo, stale)
    assert reset >= 1
    # Marker cleared → detector now finds nothing.
    assert find_stale_acceptances(fake_git_repo) == []
    # Idempotent: a second sweep resets zero rows.
    assert fix_stale_acceptances(fake_git_repo, stale) == 0


def test_audit_row_recorded_out_of_band(stub_proposal_provider, fake_git_repo):
    repo = _seed_repo_record(fake_git_repo)
    _accept_one(fake_git_repo)
    _drop_from_graph(fake_git_repo, "stub-topic")

    stale = find_stale_acceptances(fake_git_repo)
    written = record_reconcile_audit(fake_git_repo, stale, fixed=False)
    assert written == 1
    # Recompute/replace: a second report-only sweep must not stack duplicates.
    record_reconcile_audit(fake_git_repo, stale, fixed=False)

    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models import TopicAudit

    with SessionLocal() as s:
        rows = s.exec(
            select(TopicAudit)
            .where(TopicAudit.repo_id == repo.id)
            .where(TopicAudit.kind == RECONCILE_KIND)
        ).all()
    assert len(rows) == 1
    assert rows[0].code == ACCEPTED_ABSENT_CODE
    assert rows[0].fix_action == "unaccept"
    # The graph's own approved status enum is never touched by reconcile.
    assert "stub-topic" not in load_graph(fake_git_repo)["topics"]


def test_unregistered_repo_returns_empty(tmp_path):
    assert find_stale_acceptances(tmp_path) == []
