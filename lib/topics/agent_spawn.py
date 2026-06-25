"""Auto-spawn the external drafting agent for content-drift refresh proposals.

Phase 3 emits a *stub* refresh proposal (a snapshot of the drifted topic + a
note listing what changed). This module optionally hands that proposal to the
existing external-agent runner so a coding agent actually re-derives the wiki
from the current code — turning the human from author into reviewer.

Doubly gated and off by default: spawning happens only when BOTH
`topic_evolution.auto_spawn_agents` is set AND an external agent is configured
(`external_agent_configured`). Spawning is a real cost, so it stays off even
when the rest of evolution is on.

Idempotent: `start_external_proposal_run` writes a `status.json` under the
proposal dir; a proposal that already has one has already been spawned, so a
later evolve pass skips it. Best-effort — a spawn failure must never break the
evolve/cron caller.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlmodel import select

from lib.activity_log import get_activity_logger
from lib.orm import SessionLocal
from lib.orm.models import ProposalRun, Repo
from lib.settings import settings
from lib.topics import topic_dir

log = get_activity_logger("topics")

REFRESH_PROVIDER = "content-drift"


def _content_drift_run_ids(repo_path: str | Path) -> list[str]:
    """Proposal ids for this repo's content-drift runs."""
    with SessionLocal() as session:
        repo = session.exec(
            select(Repo).where(
                Repo.path == str(Path(repo_path).resolve()))).first()
        if repo is None or repo.id is None:
            return []
        return list(session.exec(
            select(ProposalRun.id).where(
                ProposalRun.repo_id == repo.id,
                ProposalRun.provider == REFRESH_PROVIDER)).all())


def _already_spawned(repo_path: str | Path, proposal_id: str) -> bool:
    """A proposal whose run dir already carries an on-disk `status.json` has
    been handed to the agent runner — don't spawn it again. Checks the FILE,
    not `load_status` (which synthesizes a status from the DB for every
    proposal, so it is never None and would skip everything)."""
    from lib.topics.proposal_external import STATUS_FILE
    out_dir = topic_dir(repo_path) / "proposals" / proposal_id
    return (out_dir / STATUS_FILE).exists()


def _topic_request(proposal: dict[str, Any]) -> str:
    """The drafting brief handed to the agent for a refresh proposal."""
    topics = proposal.get("topics") or [{}]
    topic_id = topics[0].get("id", "the topic")
    paths = (proposal.get("metadata") or {}).get("drifted_paths") or []
    listed = ", ".join(paths[:10]) if paths else "its refs"
    return (f"The code under topic '{topic_id}' changed ({listed}). "
            f"Re-derive its wiki narrative from the current refs, keeping the "
            f"topic id and structured fields stable.")


def _spawn_one(repo_path: str | Path, proposal_id: str) -> bool:
    """Hand one pending, not-yet-spawned content-drift proposal to the agent
    runner. Returns whether it spawned."""
    from lib.topics.proposals import load_proposal
    from lib.topics.proposals.external_jobs import start_external_proposal_run

    if _already_spawned(repo_path, proposal_id):
        return False
    proposal = load_proposal(repo_path, proposal_id)
    if proposal.get("status") != "pending_review":
        return False
    start_external_proposal_run(
        repo_path, run_id=proposal_id,
        topic_request=_topic_request(proposal))
    return True


def maybe_spawn_refresh_agents(repo_path: str | Path) -> int:
    """Spawn the external drafting agent for pending content-drift refresh
    proposals, up to `drift_proposal_batch_max`. A no-op (returns 0) unless
    BOTH `auto_spawn_agents` and a configured external agent. Idempotent and
    best-effort — never raises into the caller."""
    cfg = settings.topic_evolution
    if not cfg.auto_spawn_agents:
        return 0
    try:
        from lib.topics.proposal_external import external_agent_configured
        if not external_agent_configured():
            return 0
        cap = cfg.drift_proposal_batch_max
        spawned = 0
        for proposal_id in _content_drift_run_ids(repo_path):
            if cap and cap > 0 and spawned >= cap:
                break
            if _spawn_one(repo_path, proposal_id):
                spawned += 1
        if spawned:
            log.write("refresh_agents_spawned", repo_path=str(repo_path),
                      spawned=spawned)
        return spawned
    except Exception:  # noqa: BLE001 - spawning must not break the evolve caller
        log.error("refresh_agent_spawn_failed", exc_info=True)
        return 0


__all__ = ["maybe_spawn_refresh_agents"]
