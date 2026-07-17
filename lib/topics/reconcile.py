"""Reconcile stale proposal *acceptance* markers against the approved graph.

A proposal topic at ``review_status='accepted'`` is a claim that a matching
node lives in the approved graph. When that node is later deleted or renamed
*outside* the downgrade path — which already sweeps the markers via
``orm_unaccept_topic_across_proposals`` — the claim becomes a ghost: the UI
hides the Accept button on the row forever and the proposal still shows as
"accepted into" a topic that no longer exists.

This module is the *detection* half (``find_stale_acceptances``) plus a
best-effort out-of-band recorder (``record_reconcile_audit``). The *fix*
(``fix_stale_acceptances``) reuses the existing recovery primitive
``orm_unaccept_topic_across_proposals`` — we add a caller, we do not reinvent
marker-clearing.

Two deliberate constraints, both grounded in how the graph actually evolves:

* **Existence-only.** It never inspects content (refs / parent_id / blurb).
  The approved graph is legitimately *downstream* of the frozen proposal
  snapshot — ``topics scan`` refreshes refs from current files and topics get
  re-parented by hand — so content divergence is expected, not staleness.
* **Never writes the approved ``status`` enum.** The approved graph is
  human-approved and mutating a topic's ``status`` fails graph validation, so
  the stale signal lives entirely in the mutable proposal DB record plus an
  out-of-band ``topic_audits`` row.

Best-effort like the rest of ``lib/topics``: it mutates no approved graph and
returns cleanly when the repo isn't registered.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import delete, select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models import (
    ProposalRevision,
    ProposalRevisionTopic,
    ProposalRun,
    ProposalTopic,
    TopicAudit,
)
from lib.topics.core import load_graph_merged
from lib.topics.proposal_orm._common import _repo_for_path, _utc_now

log = get_activity_logger("topics")

RECONCILE_KIND = "reconcile"
ACCEPTED_ABSENT_CODE = "proposal.accepted_topic_absent"


def _graph_topic_ids(repo_path) -> set[str]:
    """Ids in the *effective* approved graph: the base graph merged with
    the ``topic.local.json`` overlay. The overlay matters — a topic accepted
    into the local layer (not yet promoted) must not be flagged absent."""
    graph = load_graph_merged(repo_path)
    return set((graph.get("topics") or {}).keys())


def _accepted_targets(session, repo_id: int) -> list[tuple[str, str, str]]:
    """``(accepted_target_id, run_id, accepted_at)`` for every proposal topic
    that currently marks ``review_status='accepted'`` in this repo, across both
    the legacy ``proposal_topics`` table and the current
    ``proposal_revision_topics`` (latest revision only). The accepted target is
    ``accepted_topic_id`` (the graph node it landed on), falling back to the
    proposal's own ``topic_id`` when the marker predates that column."""
    rows: list[tuple[str, str, str]] = []

    current = (
        select(ProposalRevisionTopic, ProposalRevision.run_id)
        .join(ProposalRevision, ProposalRevision.id == ProposalRevisionTopic.revision_id)
        .join(ProposalRun, ProposalRun.id == ProposalRevision.run_id)
        .where(ProposalRun.repo_id == repo_id)
        .where(ProposalRevision.is_latest == 1)
        .where(ProposalRevisionTopic.review_status == "accepted")
    )
    for topic, run_id in session.exec(current):
        rows.append((topic.accepted_topic_id or topic.topic_id, run_id, topic.accepted_at or ""))

    legacy = (
        select(ProposalTopic, ProposalRun.id)
        .join(ProposalRun, ProposalRun.id == ProposalTopic.run_id)
        .where(ProposalRun.repo_id == repo_id)
        .where(ProposalTopic.review_status == "accepted")
    )
    for topic, run_id in session.exec(legacy):
        rows.append((topic.accepted_topic_id or topic.topic_id, run_id, topic.accepted_at or ""))

    return rows


def find_stale_acceptances(repo_path) -> list[dict[str, Any]]:
    """Distinct topic ids that some proposal still marks
    ``review_status='accepted'`` but that are absent from the effective
    approved graph — ghost acceptances left when the topic was deleted/renamed
    outside the downgrade path.

    Each item: ``{topic_id, runs: [run_id, ...], accepted_at}`` where
    ``accepted_at`` is the most recent claim. Returns ``[]`` cleanly when the
    repo isn't registered.
    """
    with SessionLocal() as session:
        repo = _repo_for_path(session, repo_path)
        if repo is None or repo.id is None:
            return []
        present = _graph_topic_ids(repo_path)
        accepted = _accepted_targets(session, repo.id)

    by_topic: dict[str, dict[str, Any]] = {}
    for target_id, run_id, accepted_at in accepted:
        if target_id in present:
            continue
        entry = by_topic.setdefault(
            target_id, {"topic_id": target_id, "runs": [], "accepted_at": accepted_at})
        if run_id and run_id not in entry["runs"]:
            entry["runs"].append(run_id)
        if accepted_at and accepted_at > entry["accepted_at"]:
            entry["accepted_at"] = accepted_at

    for entry in by_topic.values():
        entry["runs"].sort()  # deterministic regardless of DB row order
    stale = sorted(by_topic.values(), key=lambda row: row["topic_id"])
    log.read("reconcile_scanned", stale=len(stale))
    return stale


def record_reconcile_audit(repo_path, stale: list[dict[str, Any]], *, fixed: bool) -> int:
    """Write one out-of-band ``topic_audits`` row per stale acceptance
    (``kind='reconcile'``), so the ghost-acceptance signal is auditable without
    ever touching the graph's human-approved ``status`` enum.
    ``fix_action`` records whether ``--fix`` cleared the marker (``unaccepted``)
    or it is still pending (``unaccept``). Returns rows written; 0 when there is
    nothing to record or the repo is unknown."""
    if not stale:
        return 0
    with SessionLocal() as session:
        repo = _repo_for_path(session, repo_path)
        if repo is None or repo.id is None:
            return 0
        # Recompute/replace, mirroring kind="audit" semantics: a repeated
        # report-only sweep must not stack duplicate rows — the audit reflects
        # *current* reality, not an append log.
        session.exec(
            delete(TopicAudit)
            .where(TopicAudit.repo_id == repo.id)
            .where(TopicAudit.kind == RECONCILE_KIND)
            .where(TopicAudit.code == ACCEPTED_ABSENT_CODE)
        )
        now = _utc_now()
        for item in stale:
            runs = ", ".join(item["runs"])
            session.add(TopicAudit(
                repo_id=repo.id,
                kind=RECONCILE_KIND,
                recorded_at=now,
                severity="warning",
                code=ACCEPTED_ABSENT_CODE,
                message=(f"proposal topic {item['topic_id']!r} marks "
                         f"review_status='accepted' but is absent from the "
                         f"approved graph (runs: {runs})"),
                topic_ids_json=json.dumps([item["topic_id"]]),
                fix_action="unaccepted" if fixed else "unaccept",
            ))
        session.commit()
    log.write("reconcile_audited", rows=len(stale), fixed=fixed)
    return len(stale)


def fix_stale_acceptances(repo_path, stale: list[dict[str, Any]]) -> int:
    """Clear the stale ``accepted`` markers by delegating to the existing
    recovery primitive ``orm_unaccept_topic_across_proposals`` (which sweeps
    both the legacy and current proposal tables). Returns the total number of
    proposal rows reset."""
    from lib.topics.proposal_orm import orm_unaccept_topic_across_proposals

    total = 0
    for item in stale:
        total += orm_unaccept_topic_across_proposals(repo_path, item["topic_id"])
    log.write("reconcile_fixed", topics=len(stale), rows_reset=total)
    return total
