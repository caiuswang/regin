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

import re
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

# The drift-materiality triage verdict. We act on TRIVIAL only when the agent
# says so explicitly; everything else (including a malformed/empty answer) is
# treated as MATERIAL so a real drift is never silently dropped (fail open).
_TRIAGE_RE = re.compile(r"VERDICT\s*[:=]\s*(MATERIAL|TRIVIAL)", re.IGNORECASE)


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


def _triage_prompt(topic_id: str, wiki_md: str, drifted_paths: list[str]) -> str:
    """Tag-structured brief asking whether a drift warrants a re-draft. The
    agent has read-only repo tools, so it pulls the changed files itself and
    compares them against the current wiki rather than judging a baked blob."""
    paths = "\n".join(f"- {p}" for p in drifted_paths) or "- (this topic's refs)"
    wiki_block = wiki_md.strip() or "(no wiki on file)"
    return (
        "A topic's ref files changed since its wiki was written. Decide whether "
        "the change is MATERIAL (the wiki narrative below is now inaccurate or "
        "incomplete and should be re-drafted) or TRIVIAL (formatting, comments, "
        "renames, or edits that don't change what the wiki says).\n\n"
        "Use your Read/Glob/Grep tools to read the changed files as they exist "
        "NOW, then compare against the wiki.\n\n"
        f"<topic_id>{topic_id}</topic_id>\n\n"
        f"<changed_refs>\n{paths}\n</changed_refs>\n\n"
        f"<current_wiki>\n{wiki_block}\n</current_wiki>\n\n"
        "<task>\nRead the changed refs, then answer with exactly one line:\n"
        "VERDICT: MATERIAL|TRIVIAL\n</task>"
    )


def _triage_inputs(repo_path: str | Path,
                   proposal: dict[str, Any]) -> "tuple[str, str, list] | None":
    """`(topic_id, wiki_md, drifted_paths)` for the triage prompt, or None when
    there is nothing to judge: the stub names no topic, or the topic has no
    wiki on file yet. With no wiki there is no narrative to compare the changed
    code against — materiality is undecidable — so the caller skips triage and
    lets the draft proceed (fail open)."""
    from lib.topics import slugify
    from lib.topics.wiki import wiki_dir

    topics = proposal.get("topics") or [{}]
    topic_id = topics[0].get("id") or ""
    if not topic_id:
        return None
    wiki_path = wiki_dir(repo_path) / f"{slugify(topic_id)}.md"
    if not wiki_path.is_file():
        return None
    drifted = (proposal.get("metadata") or {}).get("drifted_paths") or []
    wiki_md = wiki_path.read_text(encoding="utf-8", errors="replace")
    return topic_id, wiki_md, drifted


def _drift_is_material(repo_path: str | Path, proposal: dict[str, Any]) -> bool:
    """Agentic materiality triage for a content-drift stub: read the changed
    refs against the current wiki and decide whether a re-draft is warranted.
    Returns True (material → spawn) unless the agent explicitly says TRIVIAL.
    Fails OPEN — no wiki to compare against, no agent configured, an empty
    answer, or any error all yield True, so a real drift is never silently
    dropped."""
    try:
        from lib.memory.adapters import resolve_proposal_reviewer

        inputs = _triage_inputs(repo_path, proposal)
        if inputs is None:
            return True
        answer = resolve_proposal_reviewer().complete(
            _triage_prompt(*inputs), max_tokens=512)
        if not answer or not str(answer).strip():
            return True  # no agent / empty → fail open (spawn)
        match = _TRIAGE_RE.search(str(answer))
        is_trivial = bool(match) and match.group(1).upper() == "TRIVIAL"
        log.write("drift_triaged", proposal_id=proposal.get("id"),
                  verdict="trivial" if is_trivial else "material")
        return not is_trivial
    except Exception:  # noqa: BLE001 - triage must never drop a real drift
        log.error("drift_triage_failed", exc_info=True)
        return True


def _dismiss_trivial(repo_path: str | Path, proposal_id: str,
                     proposal: dict[str, Any]) -> None:
    """Retire a stub the agent judged TRIVIAL: ignore its topics (the same
    terminal a human reaches by clicking "ignore", so it leaves
    `pending_review`) and re-fingerprint the now-accepted content so the same
    change can't re-fire the drift on the next pass."""
    from lib.topics.proposals import ignore_proposed_topic
    from lib.topics.ref_digest import capture_ref_digests

    for topic in proposal.get("topics", []):
        tid = topic.get("id")
        if tid:
            ignore_proposed_topic(repo_path, proposal_id, tid)
            capture_ref_digests(repo_path, tid)  # advance the drift baseline
    log.write("drift_dismissed_trivial", repo_path=str(repo_path),
              proposal_id=proposal_id)


def _spawn_one(repo_path: str | Path, proposal_id: str) -> bool:
    """Hand one pending, not-yet-spawned content-drift proposal to the agent
    runner — but only after an agentic triage judges the drift MATERIAL. A
    TRIVIAL verdict dismisses the stub instead of drafting it. Returns whether
    it spawned."""
    from lib.topics.proposals import load_proposal
    from lib.topics.proposals.external_jobs import start_external_proposal_run

    if _already_spawned(repo_path, proposal_id):
        return False
    proposal = load_proposal(repo_path, proposal_id)
    if proposal.get("status") != "pending_review":
        return False
    if not _drift_is_material(repo_path, proposal):
        _dismiss_trivial(repo_path, proposal_id, proposal)
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
