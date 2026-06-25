"""Anti-runaway: expire unreviewed auto-generated topic proposals.

The evolution loop (reflect synthesis → `memory-reflect` proposals; content
drift → `content-drift` proposals) can mint proposals faster than a human
reviews them. Left unbounded the review queue rots. So each evolve pass also
retires AUTO-provider proposals that have sat unreviewed past
`auto_proposal_expire_days` — by **ignoring** them through the normal state
machine (`ignore_proposed_topic`), the same terminal a human would reach by
clicking "ignore". Human-authored proposals are never touched.

Idempotent by construction: an ignored proposal's status leaves
`pending_review`, so the next pass no longer matches it. Best-effort — pruning
must never break the evolve/cron caller.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models import ProposalRun, Repo
from lib.settings import settings

log = get_activity_logger("topics")

# Providers whose proposals are machine-minted, hence safe to auto-retire.
# `memory-reflect` = lib/memory/topic_attach; `content-drift` = content_drift.
AUTO_PROVIDERS = frozenset({"memory-reflect", "content-drift"})


def _auto_runs(repo_path: str | Path) -> list[tuple[str, str]]:
    """`(proposal_id, started_at)` for this repo's auto-provider runs."""
    with SessionLocal() as session:
        repo = session.exec(
            select(Repo).where(
                Repo.path == str(Path(repo_path).resolve()))).first()
        if repo is None or repo.id is None:
            return []
        rows = session.exec(
            select(ProposalRun.id, ProposalRun.started_at).where(
                ProposalRun.repo_id == repo.id,
                ProposalRun.provider.in_(AUTO_PROVIDERS))).all()
    return [(rid, started) for rid, started in rows]


def _is_older_than(started_at: str, cutoff: datetime) -> bool:
    """Whether an ISO `started_at` predates `cutoff`; unparseable → False
    (never expire a row we can't date)."""
    try:
        return datetime.fromisoformat(started_at) < cutoff
    except (ValueError, TypeError):
        return False


def _expire_one(repo_path: str | Path, proposal_id: str, *,
                dry_run: bool) -> bool:
    """Ignore every still-unreviewed topic of one pending proposal. Returns
    whether the proposal was expired (was pending with unreviewed topics)."""
    from lib.topics.proposals import ignore_proposed_topic, load_proposal

    proposal = load_proposal(repo_path, proposal_id)
    if proposal.get("status") != "pending_review":
        return False
    pending = [t for t in proposal.get("topics", [])
               if not t.get("review_status")]
    if not pending:
        return False
    if dry_run:
        return True
    for topic in pending:
        ignore_proposed_topic(repo_path, proposal_id, topic["id"])
    return True


def expire_stale_auto_proposals(repo_path: str | Path, *,
                                now: Optional[datetime] = None,
                                dry_run: bool = False) -> int:
    """Retire auto-provider proposals left unreviewed past
    `auto_proposal_expire_days`. Returns the count expired. `now` is injectable
    for tests; `auto_proposal_expire_days <= 0` disables. Never raises."""
    try:
        days = settings.topic_evolution.auto_proposal_expire_days
        if days <= 0:
            return 0
        cutoff = (now or datetime.now()) - timedelta(days=days)
        expired = 0
        for proposal_id, started_at in _auto_runs(repo_path):
            if not _is_older_than(started_at, cutoff):
                continue
            if _expire_one(repo_path, proposal_id, dry_run=dry_run):
                expired += 1
        if expired and not dry_run:
            log.write("auto_proposals_expired", repo_path=str(repo_path),
                      expired=expired, days=days)
        return expired
    except Exception:  # noqa: BLE001 - pruning must not break the evolve caller
        log.error("auto_proposal_expiry_failed", exc_info=True)
        return 0


__all__ = ["expire_stale_auto_proposals", "AUTO_PROVIDERS"]
