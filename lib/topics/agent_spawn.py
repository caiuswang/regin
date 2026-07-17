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
    """Proposal ids for this repo's standalone content-drift runs (the
    fallback path, used for topics with no origin proposal run)."""
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


def _origin_drift_items(repo_path: str | Path) -> list[dict[str, Any]]:
    """Origin proposal runs carrying an open content-drift note. Each item
    `{run_id, topic_id, thread_id, drifted_paths}` is a refresh that should
    land as a new revision on the *original* proposal via regenerate (the note
    rides the carry-forward rail into the agent's instructions)."""
    from lib.topics.content_drift import CONTENT_DRIFT_THREAD_KIND
    from lib.topics.proposal_orm import orm_open_content_drift_threads

    return orm_open_content_drift_threads(
        repo_path, kind=CONTENT_DRIFT_THREAD_KIND)


def _already_spawned(repo_path: str | Path, proposal_id: str) -> bool:
    """A proposal whose run dir already carries an on-disk `status.json` has
    been handed to the agent runner — don't spawn it again. Checks the FILE,
    not `load_status` (which synthesizes a status from the DB for every
    proposal, so it is never None and would skip everything)."""
    from lib.topics.proposal_external import STATUS_FILE
    out_dir = topic_dir(repo_path) / "proposals" / proposal_id
    return (out_dir / STATUS_FILE).exists()


SIBLING_WIKI_EXCERPT_CHARS = 800


def _sibling_wiki_excerpt(repo_path: str | Path, topic_id: str) -> str:
    """First ~800 chars of a sibling's current on-disk wiki, or "" when it has
    no wiki file yet. Never raises — a missing/unreadable file just yields ""."""
    from lib.topics import slugify
    from lib.topics.wiki import wiki_dir
    wiki_path = wiki_dir(repo_path) / f"{slugify(topic_id)}.md"
    if not wiki_path.is_file():
        return ""
    try:
        text = wiki_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = text.strip()
    if len(text) > SIBLING_WIKI_EXCERPT_CHARS:
        return text[:SIBLING_WIKI_EXCERPT_CHARS].rstrip() + "\n…(truncated)"
    return text


def _sibling_block(repo_path: str | Path, proposal: dict[str, Any]) -> str:
    """Format one sibling refresh proposal as a markdown block: its topic id,
    label, drifted_paths, and a short current-wiki excerpt."""
    topics = proposal.get("topics") or [{}]
    topic = topics[0]
    topic_id = topic.get("id") or "(unknown)"
    label = topic.get("label") or topic_id
    paths = (proposal.get("metadata") or {}).get("drifted_paths") or []
    paths_md = "\n".join(f"  - {p}" for p in paths) or "  - (this topic's refs)"
    excerpt = _sibling_wiki_excerpt(repo_path, topic_id)
    excerpt_md = excerpt or "(no wiki on file yet)"
    return (
        f"### {label} (`{topic_id}`)\n\n"
        f"Drifted files:\n{paths_md}\n\n"
        f"Current wiki excerpt:\n```markdown\n{excerpt_md}\n```"
    )


def _sibling_refresh_context(repo_path: str | Path, self_proposal_id: str) -> str:
    """A markdown block describing the OTHER content-drift refresh proposals
    pending in this same batch — each sibling's topic id, label, drifted_paths,
    and a short current-wiki excerpt — so the drafting agent keeps its
    cross-references consistent with siblings being rewritten alongside it.

    Returns "" when there are no siblings (and so naturally for user/external
    proposals, whose ids aren't in the content-drift set). Best-effort: a
    sibling whose proposal can't be loaded is skipped, never raised."""
    from lib.topics.proposals import load_proposal
    blocks: list[str] = []
    for proposal_id in _content_drift_run_ids(repo_path):
        if proposal_id == self_proposal_id:
            continue
        try:
            proposal = load_proposal(repo_path, proposal_id)
        except Exception:  # noqa: BLE001 - a bad sibling must not break drafting
            continue
        # Only genuinely in-flight siblings — mirror `_spawn_one`'s gate. A
        # terminal (applied/ignored) content-drift run is not "being rewritten
        # alongside you"; including it would be false and would let the block
        # grow without bound as runs accumulate in the DB.
        if proposal.get("status") != "pending_review":
            continue
        blocks.append(_sibling_block(repo_path, proposal))
    return "\n\n".join(blocks)


def _topic_request(proposal: dict[str, Any]) -> str:
    """The drafting brief handed to the agent for a refresh proposal."""
    topics = proposal.get("topics") or [{}]
    topic_id = topics[0].get("id", "the topic")
    paths = (proposal.get("metadata") or {}).get("drifted_paths") or []
    listed = ", ".join(paths[:10]) if paths else "its refs"
    return (f"The code under topic '{topic_id}' changed ({listed}). "
            f"Re-derive its wiki narrative from the current refs, keeping the "
            f"topic id and structured fields stable.")


def _triage_prompt(repo_path: str | Path, topic_id: str,
                   metadata: dict[str, Any]) -> str:
    """Build the drift-triage agent's task prompt: the same evidence pointers
    the batched judge assembles (`drift_judge.evidence_context`), one topic
    per call.

    The body is the editable ``topic-proposal-drift-triage`` surface
    (``lib/prompts/surfaces/triage.py``); this function only assembles the
    runtime context it interpolates. A broken user edit degrades to the built-in
    default inside ``render_surface`` — the prompt is never left unbuildable.
    """
    from lib.prompts import render_surface
    from lib.prompts.surfaces.triage import SURFACE_ID
    from lib.topics.drift_judge import evidence_context, feedback_cmd
    from lib.topics.ref_digest import repo_id_for_path

    item = {"drifted_paths": metadata.get("drifted_paths") or [],
            "missing_anchors": metadata.get("missing_anchors") or {}}
    context = {"topic_id": topic_id,
               "dismiss_cmd": feedback_cmd("drift-dismiss"),
               "note_cmd": feedback_cmd("drift-note"),
               **evidence_context(repo_path, topic_id, item,
                                  repo_id_for_path(repo_path))}
    return render_surface(SURFACE_ID, context)


def _triage_inputs(repo_path: str | Path,
                   proposal: dict[str, Any]) -> "tuple[str, dict] | None":
    """`(topic_id, metadata)` for the triage prompt, or None when there is
    nothing to judge: the stub names no topic, or the topic has no wiki on
    file yet. With no wiki there is no narrative to compare the changed code
    against — materiality is undecidable — so the caller skips triage and
    lets the draft proceed (fail open)."""
    from lib.topics.wiki import topic_wiki_page

    topics = proposal.get("topics") or [{}]
    topic_id = topics[0].get("id") or ""
    if not topic_id:
        return None
    if not topic_wiki_page(repo_path, topic_id).is_file():
        return None
    return topic_id, proposal.get("metadata") or {}


def _drift_is_material(repo_path: str | Path, proposal: dict[str, Any]) -> bool:
    """Agentic materiality triage for a content-drift stub: read the changed
    refs against the current wiki and decide whether a re-draft is warranted.
    Returns True (material → spawn) unless the agent explicitly says TRIVIAL.
    Fails OPEN — no wiki to compare against, no agent configured, an empty
    answer, or any error all yield True, so a real drift is never silently
    dropped."""
    try:
        from lib.memory.adapters import resolve_drift_judge
        from lib.prompts.surfaces.triage import SURFACE_ID as TRIAGE_SURFACE_ID

        inputs = _triage_inputs(repo_path, proposal)
        if inputs is None:
            return True
        # surface_id also re-resolves the agent BINDING (ExternalAgentLLM._agent
        # keys the per-surface `agent` column off this same value), not just the
        # trace tag. Before this override, triage silently inherited whatever
        # agent the review surface was bound to; its own binding column existed
        # but was dead. Passing the triage surface's own id here fixes both the
        # trace mislabel and that dead binding in one change — intentional, not
        # a side effect to guard against.
        answer = resolve_drift_judge().complete(
            _triage_prompt(repo_path, *inputs), max_tokens=512, cwd=repo_path,
            surface_id=TRIAGE_SURFACE_ID)
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
                     proposal: dict[str, Any], reason: str = "") -> None:
    """Retire a stub the agent judged TRIVIAL — the safety net behind the
    judge's own `drift-dismiss` command: the verdict reason lands on the
    drift threads, both surfaces are dismissed, and the baseline advances.
    A no-op (beyond re-capture) when the agent already dismissed it."""
    from lib.topics.content_drift import judge_dismiss_drift

    for topic in proposal.get("topics", []):
        tid = topic.get("id")
        if tid:
            judge_dismiss_drift(repo_path, tid, reason)
    log.write("drift_dismissed_trivial", repo_path=str(repo_path),
              proposal_id=proposal_id)


def _verdict_material(repo_path: str | Path, proposal: dict[str, Any],
                      verdicts: "dict[str, dict[str, str]] | None") -> bool:
    """Materiality for one drift item: the batched judge's verdict when it
    ran (a topic its answer didn't cover counts material — fail open),
    otherwise the per-item triage."""
    if verdicts is None:
        return _drift_is_material(repo_path, proposal)
    topics = proposal.get("topics") or [{}]
    verdict = verdicts.get(topics[0].get("id") or "")
    return verdict is None or verdict.get("verdict") != "trivial"


def _verdict_reason(proposal: dict[str, Any],
                    verdicts: "dict[str, dict[str, str]] | None") -> str:
    topics = proposal.get("topics") or [{}]
    verdict = (verdicts or {}).get(topics[0].get("id") or "") or {}
    return verdict.get("reason") or ""


def _spawn_one(repo_path: str | Path, proposal_id: str,
               verdicts: "dict[str, dict[str, str]] | None" = None) -> bool:
    """Hand one pending, not-yet-spawned content-drift proposal to the agent
    runner — but only after the drift is judged MATERIAL (batched judge when
    available, else per-item triage). A TRIVIAL verdict dismisses the stub
    instead of drafting it. Returns whether it spawned."""
    from lib.topics.proposals import load_proposal
    from lib.topics.proposals.external_jobs import start_external_proposal_run

    if _already_spawned(repo_path, proposal_id):
        return False
    proposal = load_proposal(repo_path, proposal_id)
    if proposal.get("status") != "pending_review":
        return False
    if not _verdict_material(repo_path, proposal, verdicts):
        _dismiss_trivial(repo_path, proposal_id, proposal,
                         _verdict_reason(proposal, verdicts))
        return False
    start_external_proposal_run(
        repo_path, run_id=proposal_id,
        topic_request=_topic_request(proposal))
    return True


_ACTIVE_RUN_STATES = {"queued", "running", "waiting_for_permission"}


def _proposal_for_drift_item(item: dict[str, Any]) -> dict[str, Any]:
    """A minimal proposal-shaped dict so `_drift_is_material`/`_topic_request`
    can triage and brief an origin-run drift note the same way they do a
    standalone refresh stub."""
    return {
        "id": item["run_id"],
        "topics": [{"id": item.get("topic_id")}],
        "metadata": {"drifted_paths": item.get("drifted_paths") or []},
    }


def _dismiss_drift_thread(repo_path: str | Path, item: dict[str, Any],
                          reason: str = "") -> None:
    """Retire an origin-run drift note the agent judged TRIVIAL — the safety
    net behind the judge's own `drift-dismiss` command: the verdict reason
    lands on the thread, both surfaces are dismissed, and the baseline
    advances. A no-op (beyond re-capture) when the agent already dismissed
    it."""
    from lib.topics.content_drift import judge_dismiss_drift
    from lib.topics.proposal_orm import orm_set_feedback_thread_resolution

    if item.get("topic_id"):
        judge_dismiss_drift(repo_path, item["topic_id"], reason)
    else:
        orm_set_feedback_thread_resolution(
            repo_path, item["run_id"], item["thread_id"],
            resolution_state="dismissed")
    log.write("drift_dismissed_trivial", repo_path=str(repo_path),
              proposal_id=item["run_id"], topic_id=item.get("topic_id"))


def _spawn_one_via_regenerate(repo_path: str | Path, item: dict[str, Any],
                              verdicts: "dict[str, dict[str, str]] | None" = None
                              ) -> bool:
    """Drive one origin-run drift note to a refresh revision by regenerating
    that run — but only after the drift is judged MATERIAL (batched judge
    when available, else per-item triage). A TRIVIAL verdict dismisses the
    note instead. Skips a run that is already mid-flight. Returns whether it
    triggered a regenerate."""
    from lib.topics.proposals import load_proposal_status
    from lib.topics.proposals.external_jobs import start_external_regenerate_run

    status = load_proposal_status(repo_path, item["run_id"]) or {}
    if status.get("state") in _ACTIVE_RUN_STATES:
        return False
    proposal = _proposal_for_drift_item(item)
    if not _verdict_material(repo_path, proposal, verdicts):
        _dismiss_drift_thread(repo_path, item,
                              _verdict_reason(proposal, verdicts))
        return False
    start_external_regenerate_run(repo_path, item["run_id"])
    return True


def _spawn_standalone_refreshes(repo_path: str | Path, cap: int, spawned: int,
                                verdicts: "dict[str, dict[str, str]] | None"
                                ) -> int:
    """Spawn fresh drafts for the legacy standalone content-drift proposals
    (topics with no origin run). Returns the updated spawned count."""
    for proposal_id in _content_drift_run_ids(repo_path):
        if cap and cap > 0 and spawned >= cap:
            break
        if _spawn_one(repo_path, proposal_id, verdicts):
            spawned += 1
    return spawned


def _spawn_origin_refreshes(repo_path: str | Path, cap: int, spawned: int,
                            verdicts: "dict[str, dict[str, str]] | None"
                            ) -> int:
    """Regenerate origin runs carrying an open drift note (topics whose whole
    lifecycle stays in their original proposal). Returns the updated count."""
    for item in _origin_drift_items(repo_path):
        if cap and cap > 0 and spawned >= cap:
            break
        if _spawn_one_via_regenerate(repo_path, item, verdicts):
            spawned += 1
    return spawned


def _standalone_judge_item(repo_path: str | Path,
                           proposal_id: str) -> "dict[str, Any] | None":
    """One pending, not-yet-spawned standalone stub as a judge item, or None
    when it is spawned, terminal, or unloadable."""
    from lib.topics.proposals import load_proposal

    if _already_spawned(repo_path, proposal_id):
        return None
    try:
        proposal = load_proposal(repo_path, proposal_id)
    except Exception:  # noqa: BLE001 - a bad stub must not break the sweep
        return None
    if proposal.get("status") != "pending_review":
        return None
    topics = proposal.get("topics") or [{}]
    metadata = proposal.get("metadata") or {}
    return {
        "run_id": proposal_id,
        "topic_id": topics[0].get("id"),
        "drifted_paths": metadata.get("drifted_paths") or [],
        "missing_anchors": metadata.get("missing_anchors") or {},
    }


def _pending_judge_items(repo_path: str | Path) -> list[dict[str, Any]]:
    """Every pending drift item from both surfaces, shaped for the batched
    judge: origin-run notes as returned by `_origin_drift_items`, plus
    pending, not-yet-spawned standalone stubs."""
    items = list(_origin_drift_items(repo_path))
    for proposal_id in _content_drift_run_ids(repo_path):
        item = _standalone_judge_item(repo_path, proposal_id)
        if item is not None:
            items.append(item)
    return [item for item in items if item.get("topic_id")]


def maybe_spawn_refresh_agents(repo_path: str | Path) -> int:
    """Spawn the drafting agent for pending content-drift refreshes, up to
    `drift_proposal_batch_max`. Two sources share the cap: origin-run drift
    notes (regenerated into a revision on the original proposal) and legacy
    standalone content-drift proposals (drafted fresh). A no-op (returns 0)
    unless BOTH `auto_spawn_agents` and a configured external agent. Idempotent
    and best-effort — never raises into the caller."""
    cfg = settings.topic_evolution
    if not cfg.auto_spawn_agents:
        return 0
    try:
        from lib.topics.drift_judge import judge_drift_batch
        from lib.topics.proposal_external import external_agent_configured
        if not external_agent_configured():
            return 0
        cap = cfg.drift_proposal_batch_max
        # One judge pass over every pending item; None (judge unavailable /
        # failed) makes the loops fall back to the per-item triage.
        verdicts = judge_drift_batch(repo_path, _pending_judge_items(repo_path))
        spawned = _spawn_origin_refreshes(repo_path, cap, 0, verdicts)
        spawned = _spawn_standalone_refreshes(repo_path, cap, spawned, verdicts)
        if spawned:
            log.write("refresh_agents_spawned", repo_path=str(repo_path),
                      spawned=spawned)
        return spawned
    except Exception:  # noqa: BLE001 - spawning must not break the evolve caller
        log.error("refresh_agent_spawn_failed", exc_info=True)
        return 0


__all__ = ["maybe_spawn_refresh_agents"]
