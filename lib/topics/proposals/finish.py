"""Notify-on-finish ingestion for external-agent topic proposal runs.

The drafting agent runs as a subprocess. Rather than the server blocking on
that subprocess up to a timeout — which kills long drafts mid-flight and
loses their work — the agent calls `regin topics proposal-finish <id>` as
its final step. That command runs *in the agent's own process* and is the
authoritative ingest: it reads the agent's output JSON, validates it,
persists the proposal + wiki, and stamps the run `completed` with an
`agent_signaled` marker. The server-side runner reads that marker and skips
a redundant re-ingest.

Idempotent: a call after the run already reached a terminal state (or a
second call) is a no-op. A run whose agent session ends *without* ever
calling this is reaped as `failed` (see `reap.py`) — the explicit failure
the old silent-timeout path never produced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.activity_log import get_activity_logger
from lib.topics import TopicGraphError, topic_dir, utc_now

_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


def _noop_result(proposal_id: str, state: str | None) -> dict[str, Any]:
    return {"proposal_id": proposal_id, "state": state, "ingested": False}


def _parse_agent_output(repo: Path, out_dir: Path) -> tuple[dict[str, Any], str]:
    """Load + validate the agent's output JSON (temp first, then canonical).

    Mirrors the runner's exit-path parse so a signal-driven ingest applies
    the same contract: known schema, real ref paths, non-empty wiki.
    """
    from lib.topics.proposal_external import (
        OUTPUT_FILE, TEMP_OUTPUT_DIR, TEMP_OUTPUT_FILE,
        _apply_regenerate_scope, _load_agent_payload,
        _normalise_agent_payload, _validate_paths,
    )

    temp_output = out_dir / TEMP_OUTPUT_DIR / TEMP_OUTPUT_FILE
    canonical = out_dir / OUTPUT_FILE
    source = temp_output if temp_output.exists() else canonical
    payload = _load_agent_payload(source, "")
    proposal, wiki = _normalise_agent_payload(repo, payload)
    _validate_paths(repo, proposal)
    # Scoped content-drift regenerate: splice the drifted topics back over the
    # prior full draft so untouched wikis stay verbatim. Must run here too —
    # the agent self-ingests in its own process on the notify-on-finish path.
    proposal, wiki = _apply_regenerate_scope(repo, out_dir, proposal, wiki)
    if temp_output.exists() and not canonical.exists():
        canonical.write_text(temp_output.read_text())
    return proposal, wiki


def finish_proposal_run(
    repo_path: str | Path, proposal_id: str, *, source: str = "agent",
) -> dict[str, Any]:
    """Ingest a finished proposal run on the agent's explicit signal.

    Returns a small result dict. Raises `TopicGraphError` when there is no
    such run, or when the agent signalled completion but left no valid
    output (the run is stamped `failed` first, so the failure is visible).
    """
    repo = Path(repo_path)
    out_dir = topic_dir(repo) / "proposals" / proposal_id
    log = get_activity_logger("topics")

    from lib.topics.proposal_external import load_status, write_status

    status = load_status(out_dir)
    if status is None:
        raise TopicGraphError(f"no proposal run to finish: {proposal_id}")
    if status.get("agent_signaled") or status.get("state") in _TERMINAL_STATES:
        log.read(
            "proposal_finish_noop",
            proposal_id=proposal_id, state=status.get("state"),
        )
        return _noop_result(proposal_id, status.get("state"))

    try:
        proposal, wiki = _parse_agent_output(repo, out_dir)
    except Exception as exc:
        status.update(
            state="failed",
            error=f"proposal-finish: invalid agent output: {exc}",
            completed_at=utc_now(),
        )
        write_status(out_dir, status)
        log.error("proposal_finish_invalid", proposal_id=proposal_id, exc_info=True)
        raise TopicGraphError(
            f"proposal-finish: invalid agent output: {exc}"
        ) from exc

    from lib.topics.proposal_drafting import _write_proposal_artifacts
    from .core_io import list_proposal_revisions

    proposal["provider"] = "external-agent"
    proposal["status"] = "pending_review"
    # A regenerate run already carries the prior draft as a revision, so the
    # ingest must *append* a new `regenerated` revision rather than overwrite
    # the prior one in place (which a "generated"/append=False write does).
    # An initial run has no revision yet → fresh `generated` revision.
    is_regenerate = bool(list_proposal_revisions(repo, proposal_id))
    _write_proposal_artifacts(
        out_dir, proposals=proposal, wiki=wiki,
        repo_path=repo, proposal_id=proposal_id,
        append_revision=is_regenerate,
        revision_kind="regenerated" if is_regenerate else "generated",
    )
    status.update(
        state="completed",
        agent_signaled=True,
        signaled_by=source,
        completed_at=utc_now(),
        error=None,
    )
    write_status(out_dir, status)
    log.write("proposal_finish_ingested", proposal_id=proposal_id, source=source)
    # This self-ingest is the authoritative completion in the notify-on-finish
    # design — the server-runner exit may never observe it — so the inbox
    # `proposal.ready` event must fire here, not only on the runner path.
    # Best-effort: a notify must never break the ingest it announces.
    try:
        from lib.topics.proposal_external import notify_proposal_ready
        notify_proposal_ready(repo, proposal_id, status.get("agent"))
    except Exception:  # noqa: BLE001 — notify is cosmetic; ingest already stuck
        log.error("proposal_finish_notify_failed", proposal_id=proposal_id, exc_info=True)
    return {"proposal_id": proposal_id, "state": "completed", "ingested": True}
