"""Load / save / list / delete + review-state transitions for proposal runs.

ORM-first reads with disk fallback for repos not yet imported; ORM-only
writes (disk topics.json writes were removed in Phase E2). The disk
side still owns evidence.json, wiki.md, instructions.md, agent-output.json,
and status.json under `.regin/topics/proposals/<id>/`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from lib.topics import TopicGraphError, topic_dir, utc_now
from lib.topics.proposal_drafting import _draft_proposal, _write_proposal_artifacts

from ._common import (
    VALID_PROPOSAL_REVIEW_STATES,
    _guard_regenerate_not_in_flight,
    _resolve_prompt_templates,
    _topics_log,
)


def create_proposal_run(
    repo_path: str | Path,
    *,
    run_id: str | None = None,
    agent: str | None = None,
    topic_request: str | None = None,
    prompt_template_ids: list[str] | None = None,
) -> dict[str, Path]:
    """Synchronously run the external agent and write draft proposal artifacts.

    The agent explores the repo with its own tools; there is no evidence
    pack. The web uses the async `start_external_proposal_run`; this sync
    entry is for direct/programmatic callers.
    """
    repo = Path(repo_path)
    proposal_id = run_id or utc_now().replace(":", "").replace("-", "")
    out_dir = topic_dir(repo) / "proposals" / proposal_id
    out_dir.mkdir(parents=True, exist_ok=True)

    templates = _resolve_prompt_templates(prompt_template_ids)
    proposals, wiki = _draft_proposal(
        repo=repo,
        out_dir=out_dir,
        proposal_id=proposal_id,
        topic_request=topic_request,
        agent=agent,
        prompt_templates=templates,
    )
    proposals["status"] = "pending_review"
    artifacts = _write_proposal_artifacts(
        out_dir, proposals=proposals, wiki=wiki,
        repo_path=repo, proposal_id=proposal_id,
        revision_kind="generated",
    )
    _topics_log().write(
        "topic_proposal_created",
        proposal_id=proposal_id,
        repo_path=str(repo),
        agent=agent,
        topic_count=len(proposals.get("topics", []) or []),
    )
    # Gated, best-effort LLM review note (no-op unless auto_review_notes).
    from lib.topics.proposal_review import maybe_generate_review_note
    maybe_generate_review_note(repo, proposal_id)
    return artifacts


def load_proposal(repo_path: str | Path, proposal_id: str) -> dict[str, Any]:
    """ORM-first; disk fallback for proposals not yet imported.

    `save_proposal` (and `_write_proposal_artifacts`) only write to the
    ORM — disk `topics.json` writes were removed in Phase E2. Reading
    disk-first here meant any pre-cutover proposal with a stale
    `topics.json` shadowed every subsequent ORM write. Mirror what
    `list_proposal_runs` already does: prefer ORM, fall back to disk
    only when no row exists (the path the import script relies on).
    """
    from lib.topics.proposal_orm import orm_load_proposal
    orm_dict = orm_load_proposal(repo_path, proposal_id)
    if orm_dict is not None:
        return orm_dict
    path = topic_dir(repo_path) / "proposals" / proposal_id / "topics.json"
    if path.exists():
        return json.loads(path.read_text())
    raise TopicGraphError(f"proposal not found: {proposal_id}")


def load_proposal_revision(repo_path: str | Path, proposal_id: str, revision_id: int) -> dict[str, Any]:
    from lib.topics.proposal_orm import orm_load_proposal_revision

    orm_dict = orm_load_proposal_revision(repo_path, proposal_id, revision_id)
    if orm_dict is not None:
        return orm_dict
    raise TopicGraphError(f"proposal revision not found: {proposal_id}:{revision_id}")


def load_proposal_status(repo_path: str | Path, proposal_id: str) -> dict[str, Any]:
    """ORM-first status; disk fallback for repos not yet imported."""
    from lib.topics.proposal_orm import orm_load_proposal_status
    orm_status = orm_load_proposal_status(repo_path, proposal_id)
    if orm_status is not None:
        return orm_status
    base = (topic_dir(repo_path) / "proposals").resolve()
    path = (base / proposal_id).resolve()
    if base not in path.parents:
        raise TopicGraphError(f"invalid proposal id: {proposal_id}")
    status_path = path / "status.json"
    if status_path.exists():
        return json.loads(status_path.read_text())
    topics_path = path / "topics.json"
    if topics_path.exists():
        return {"state": "completed", "trace_id": None, "agent": None, "error": None}
    if path.exists():
        return {"state": "unknown", "trace_id": None, "agent": None, "error": None}
    raise TopicGraphError(f"proposal run not found: {proposal_id}")


def save_proposal(repo_path: str | Path, proposal_id: str, proposal: dict[str, Any]) -> None:
    """ORM-only — Phase E disk-write cleanup landed.

    The disk topics.json file is no longer written. Tests that planted
    state via `paths["topics"].write_text(...)` have migrated to call
    this function; legacy disk files (from before this cleanup) are
    still readable via `load_proposal`'s disk fallback.
    """
    from lib.topics.proposal_orm import orm_save_proposal
    orm_save_proposal(repo_path, proposal_id, proposal)


def set_proposal_review_state(
    repo_path: str | Path,
    proposal_id: str,
    review_state: str,
) -> dict[str, Any]:
    if review_state not in VALID_PROPOSAL_REVIEW_STATES:
        raise TopicGraphError(
            f"review_state must be one of {sorted(VALID_PROPOSAL_REVIEW_STATES)}"
        )
    proposal = load_proposal(repo_path, proposal_id)
    proposal["status"] = review_state
    save_proposal(repo_path, proposal_id, proposal)
    _topics_log().write(
        "proposal_review_state_changed",
        proposal_id=proposal_id, review_state=review_state,
        repo_path=str(repo_path),
    )
    return proposal


def restore_proposal_to_revision(
    repo_path: str | Path,
    proposal_id: str,
    source_revision_id: int,
) -> dict[str, Any]:
    """Append a new "restored" revision whose content is copied from a
    historical revision of the same proposal run.

    Mirrors `regenerate_proposal_run`'s shape (new revision, run state
    reset to `pending_review`, accept markers cleared) but the new
    revision's body comes from an existing revision instead of a
    provider invocation.

    Also rewrites the on-disk `proposals/<id>/wiki.md` to match the
    restored content — the apply path (web/blueprints/topics/apply.py)
    and `_persist_per_topic_wiki` both read the approved wiki from that
    file, so leaving stale content there means a subsequent Apply would
    publish the previous revision's wiki instead of the restored one.
    """
    _guard_regenerate_not_in_flight(repo_path, proposal_id)
    from lib.topics.proposal_orm import orm_restore_proposal_to_revision
    proposal = orm_restore_proposal_to_revision(repo_path, proposal_id, source_revision_id)
    if proposal is None:
        raise TopicGraphError(
            f"proposal revision not found: {proposal_id}:{source_revision_id}"
        )
    wiki_body = proposal.get("wiki") or ""
    if wiki_body:
        wiki_path = topic_dir(repo_path) / "proposals" / proposal_id / "wiki.md"
        wiki_path.write_text(wiki_body)
    _topics_log().write(
        "proposal_restored_from_revision",
        proposal_id=proposal_id,
        source_revision_id=source_revision_id,
        repo_path=str(repo_path),
    )
    return proposal


def list_proposal_revisions(repo_path: str | Path, proposal_id: str) -> list[dict[str, Any]]:
    from lib.topics.proposal_orm import orm_list_proposal_revisions

    return orm_list_proposal_revisions(repo_path, proposal_id)


def _run_id_to_iso(run_id: str) -> str | None:
    """Reverse the `utc_now().replace(':','').replace('-','')` id stamp
    (e.g. `20260523T160359Z`) back to ISO `2026-05-23T16:03:59Z`. Used as
    the last-activity fallback for disk-only runs, whose creation id is the
    only timestamp available. Returns None if the id isn't that shape."""
    if len(run_id) != 16 or run_id[8] != "T" or run_id[15] != "Z":
        return None
    if not run_id[:8].isdigit() or not run_id[9:15].isdigit():
        return None
    return (
        f"{run_id[0:4]}-{run_id[4:6]}-{run_id[6:8]}T"
        f"{run_id[9:11]}:{run_id[11:13]}:{run_id[13:15]}Z"
    )


def _disk_run_row(repo_path: str | Path, child: Path) -> dict[str, Any]:
    topics_path = child / "topics.json"
    evidence_path = child / "evidence.json"
    wiki_path = child / "wiki.md"
    status = load_proposal_status(repo_path, child.name)
    trace_id = status.get("trace_id")
    return {
        "id": child.name,
        "path": str(child),
        "has_topics": topics_path.exists(),
        "has_evidence": evidence_path.exists(),
        "has_wiki": wiki_path.exists(),
        "state": status.get("state", "completed" if topics_path.exists() else "unknown"),
        "trace_id": trace_id,
        "trace_url": f"/sessions/{trace_id}" if trace_id else None,
        "agent_trace_id": status.get("agent_trace_id"),
        "agent_trace_url": status.get("agent_trace_url"),
        "agent": status.get("agent"),
        "error": status.get("error"),
        "last_activity_at": (
            status.get("completed_at") or status.get("started_at")
            or _run_id_to_iso(child.name)
        ),
    }


def _orm_run_row(run: dict[str, Any]) -> dict[str, Any]:
    child = Path(run["path"])
    evidence_path = child / "evidence.json"
    status = run["status"]
    trace_id = status.get("trace_id")
    return {
        "id": run["id"],
        "path": run["path"],
        "has_topics": run["has_topics"],
        "has_evidence": evidence_path.exists(),
        "has_wiki": run["has_wiki"],
        "state": status.get("state", "completed" if run["has_topics"] else "unknown"),
        "trace_id": trace_id,
        "trace_url": f"/sessions/{trace_id}" if trace_id else None,
        "agent_trace_id": status.get("agent_trace_id"),
        "agent_trace_url": status.get("agent_trace_url"),
        "agent": status.get("agent"),
        "error": status.get("error"),
        "last_activity_at": run.get("last_activity_at"),
    }


def list_proposal_runs(repo_path: str | Path) -> list[dict[str, Any]]:
    """List proposal runs for a repo, newest first.

    ORM-first (Phase E2). Falls back to disk scanning for repos that
    haven't been imported yet — that's the back-compat hatch the import
    script's idempotency relies on.
    """
    from lib.topics.proposal_orm import orm_list_proposal_runs

    orm_rows = orm_list_proposal_runs(repo_path)
    rows: list[dict[str, Any]] = [_orm_run_row(run) for run in orm_rows]
    seen_ids: set[str] = {run["id"] for run in rows}

    base = topic_dir(repo_path) / "proposals"
    # Back-compat fallback: surface any on-disk-only proposals (e.g.
    # mid-upgrade state where the import script hasn't run). Skipping
    # those here would hide legitimate work from the user.
    if base.exists():
        for child in sorted(base.iterdir(), reverse=True):
            if not child.is_dir() or child.name in seen_ids:
                continue
            rows.append(_disk_run_row(repo_path, child))
    rows.sort(key=lambda r: r["id"], reverse=True)
    return rows


def backfill_disk_proposals_to_orm(repo_path: str | Path) -> dict[str, int]:
    """Upsert any on-disk proposal directories into the `proposal_runs`
    ORM table. Idempotent — existing rows are skipped by
    `orm_create_proposal_run`.

    Needed because regin historically wrote proposals only to disk;
    actions that go through the ORM (feedback threads, status updates)
    fail with "proposal run not found" for those legacy runs. Sync from
    git calls this so a teammate-shipped `.regin/topics/proposals/`
    becomes fully usable on the receiving machine.
    """
    from lib.topics.proposal_orm import (
        orm_create_proposal_run, orm_list_proposal_runs,
        orm_save_proposal, orm_update_proposal_status,
    )

    base = topic_dir(repo_path) / "proposals"
    if not base.exists():
        return {"scanned": 0, "imported": 0}

    existing_ids = {run["id"] for run in orm_list_proposal_runs(repo_path)}
    scanned = 0
    imported = 0
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        scanned += 1
        if child.name in existing_ids:
            continue
        topics_path = child / "topics.json"
        wiki_path = child / "wiki.md"
        try:
            status = load_proposal_status(repo_path, child.name)
        except TopicGraphError:
            status = {}
        if topics_path.exists():
            # Full content import: orm_save_proposal upserts the run row
            # AND a revision with topics, using provider / topic_request
            # from the proposal payload.
            proposal = json.loads(topics_path.read_text())
            wiki = wiki_path.read_text() if wiki_path.exists() else None
            orm_save_proposal(repo_path, child.name, proposal, wiki=wiki)
        else:
            # No topics yet (e.g. an external-agent run that wrote
            # status.json before drafting). Create a minimal row so
            # feedback / status updates can find it.
            orm_create_proposal_run(
                repo_path,
                child.name,
                provider=status.get("agent") or "external",
                state=status.get("state") or "queued",
                agent=status.get("agent"),
                started_at=status.get("started_at"),
            )
        # Apply state from status.json (orm_save_proposal hardcodes
        # state="completed" on insert, which is right for proposals with
        # topics.json but masks failed/stopped runs that recorded a
        # different state on disk).
        disk_state = status.get("state")
        if disk_state and disk_state != "completed":
            orm_update_proposal_status(repo_path, child.name, state=disk_state)
        imported += 1
    _topics_log().write(
        "proposal_backfill_to_orm",
        repo_path=str(repo_path), scanned=scanned, imported=imported,
    )
    return {"scanned": scanned, "imported": imported}


def delete_proposal_run(repo_path: str | Path, proposal_id: str) -> dict[str, Any]:
    """Delete a proposal run from both ORM and disk."""
    from lib.topics.proposal_orm import orm_delete_proposal_run

    if not proposal_id or "/" in proposal_id or "\\" in proposal_id or proposal_id in {".", ".."}:
        raise TopicGraphError(f"invalid proposal id: {proposal_id}")
    base = (topic_dir(repo_path) / "proposals").resolve()
    path = (base / proposal_id).resolve()
    if base not in path.parents:
        raise TopicGraphError(f"invalid proposal id: {proposal_id}")

    deleted_orm = orm_delete_proposal_run(repo_path, proposal_id)
    deleted_disk = False
    if path.exists() and path.is_dir():
        shutil.rmtree(path)
        deleted_disk = True

    if not (deleted_orm or deleted_disk):
        raise TopicGraphError(f"proposal run not found: {proposal_id}")
    _topics_log().write(
        "topic_proposal_deleted",
        proposal_id=proposal_id,
        repo_path=str(repo_path),
        deleted_orm=deleted_orm,
        deleted_disk=deleted_disk,
    )
    return {"id": proposal_id, "deleted": True}


# States a run can be stopped from — anything else is already terminal.
ACTIVE_RUN_STATES = frozenset({"queued", "running", "waiting_for_permission"})


def stop_proposal_run(repo_path: str | Path, proposal_id: str) -> dict[str, Any]:
    """Cancel an in-flight proposal run.

    Terminates the agent subprocess (if still live, via the in-process
    `run_control` registry) and stamps the run `cancelled` for immediate
    UI feedback. The worker thread independently stamps `cancelled` when
    it notices the kill — both writes are idempotent. A no-op when the run
    already reached a terminal state, so a double-click returns
    `already_terminal` rather than clobbering a completed/failed run.

    Best-effort on the boundary: if the agent finishes successfully in the
    narrow window between this active-state check and the worker's terminal
    write, the run may land `completed` rather than `cancelled`. That's the
    honest outcome (the work did finish) — we don't fabricate a cancel.
    """
    repo = Path(repo_path)
    status = load_proposal_status(repo, proposal_id)  # raises TopicGraphError if missing
    state = status.get("state")
    if state not in ACTIVE_RUN_STATES:
        return {"id": proposal_id, "stopped": False, "already_terminal": True, "state": state}

    from . import run_control
    from lib.topics.proposal_external import write_status

    signalled = run_control.request_cancel(proposal_id)
    out_dir = topic_dir(repo) / "proposals" / proposal_id
    write_status(out_dir, {
        **status,
        "state": "cancelled",
        "error": None,
        "completed_at": utc_now(),
    })
    _topics_log().write(
        "topic_proposal_stopped",
        proposal_id=proposal_id,
        repo_path=str(repo),
        prior_state=state,
        signalled=signalled,
    )
    return {"id": proposal_id, "stopped": True, "signalled": signalled, "state": "cancelled"}

