"""Files and status an external proposal run exchanges through its out_dir,
and normalisation of the payload the drafting agent returns."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lib.topics import TopicGraphError, utc_now


STATUS_FILE = "status.json"
OUTPUT_FILE = "agent-output.json"
TOPIC_MAP_FILE = "topic-map.json"
PRIOR_PROPOSAL_FILE = "prior-proposal.json"
TEMP_OUTPUT_DIR = ".tmp"
TEMP_OUTPUT_FILE = "agent-output.json"


def _repo_and_id_from_out_dir(out_dir: Path) -> tuple[Path, str]:
    """Derive (repo_path, proposal_id) from an out_dir.

    out_dir is always `<repo>/.regin/topics/proposals/<id>` — this
    convention has been load-bearing since the proposal feature
    shipped and is locked in by `lib.topics.core.topic_dir`.
    """
    proposal_id = out_dir.name
    repo_path = out_dir.parents[3]  # proposals/ → topics/ → .regin/ → repo
    return repo_path, proposal_id


def write_status(out_dir: Path, status: dict[str, Any]) -> dict[str, Any]:
    """Persist proposal run status to the ORM (the authoritative store).

    Phase E2 made the ORM the source of truth; the disk `status.json` is no
    longer written, so `load_status` reads it disk-first only for legacy
    dirs and otherwise falls back to the ORM. The finish signal therefore
    crosses the agent-subprocess → server boundary through the shared
    SQLite DB (both resolve the same file), not through disk.
    """
    from lib.topics.proposal_orm import (
        orm_create_proposal_run, orm_update_proposal_status,
    )
    status = {**status, "updated_at": utc_now()}
    repo_path, proposal_id = _repo_and_id_from_out_dir(out_dir)

    column_fields = {
        "state", "trace_id", "started_at", "completed_at",
        "error", "error_detail",
    }
    # `proposal_status` is the *review-state* axis (pending_review →
    # ready_to_apply → applied), owned solely by the proposal-save / apply
    # paths. It is orthogonal to the run *lifecycle* status this function
    # writes. `load_status` (→ _run_to_status_dict) spreads the whole
    # metadata bag — including proposal_status — into the status dict, so
    # without this exclusion any write_status round-trips a stale value
    # back over a fresh one: a regenerate's finish ingest sets the new
    # revision to pending_review, then write_status re-stamps the prior
    # `applied`, stranding the draft as un-appliable. Never carry it here.
    review_state_fields = {"proposal_status"}
    metadata_patch = {
        k: v for k, v in status.items()
        if k not in column_fields
        and k not in review_state_fields
        and k not in {"agent", "updated_at"}
    }

    orm_create_proposal_run(
        repo_path, proposal_id,
        provider="external-agent",
        state=status.get("state") or "queued",
        agent=status.get("agent"),
        started_at=status.get("started_at"),
        prompt_template_ids=status.get("prompt_template_ids") or [],
        metadata=metadata_patch,
    )
    orm_update_proposal_status(
        repo_path, proposal_id,
        state=status.get("state"),
        completed_at=status.get("completed_at"),
        error=status.get("error"),
        error_detail=status.get("error_detail"),
        metadata_patch=metadata_patch,
        clear_error="error" in status,
        clear_error_detail="error_detail" in status,
        clear_completed_at="completed_at" in status,
    )
    return status


def load_status(out_dir: Path) -> dict[str, Any] | None:
    """Disk-first read of proposal status during the dual-write window;
    ORM fallback when the disk file is missing."""
    path = out_dir / STATUS_FILE
    if path.exists():
        return json.loads(path.read_text())
    from lib.topics.proposal_orm import orm_load_proposal_status
    repo_path, proposal_id = _repo_and_id_from_out_dir(out_dir)
    return orm_load_proposal_status(repo_path, proposal_id)


def _load_agent_payload(output_path: Path, stdout: str) -> dict[str, Any]:
    if output_path.exists():
        return json.loads(output_path.read_text())
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stdout, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    return json.loads(stdout)


def _topic_wiki_section(topic: dict[str, Any]) -> str | None:
    """One topic's contribution to the combined wiki, or None if it has no
    page. Normalizes to a level-2 (`## `) section so the combined doc always
    has section boundaries (a leading `# ` h1 is demoted to `## `, an h2 is
    kept, anything else is headed with the topic label)."""
    body = str(topic.get("wiki") or "").strip()
    if not body:
        return None
    if body.startswith("## "):
        return body
    if body.startswith("# "):
        return "#" + body  # demote h1 -> h2 so the combined doc keeps `##` boundaries
    label = topic.get("label") or topic.get("id") or "Topic"
    return f"## {label}\n\n{body}"


def _combined_proposal_wiki(payload: dict[str, Any], topics: list[dict[str, Any]]) -> str:
    """Derive the revision-level combined wiki from the per-topic pages.

    Per-topic `wiki` is the source of truth; this stitches an ``overview``
    intro plus one ``## label`` section per topic into a single document for
    revision-level storage, legacy display, and the shared intro. Falls back
    to a legacy top-level ``wiki`` string when no topic carries its own page.
    """
    sections = [s for s in (_topic_wiki_section(t) for t in topics) if s]
    if not sections:
        return str(payload.get("wiki") or "").strip()
    overview = str(payload.get("overview") or "").strip()
    parts = ([overview] if overview else []) + sections
    return "\n\n".join(parts).strip()


def _strip_review_markers(topics: list[Any]) -> None:
    """Review markers are server-owned bookkeeping; an agent echoing them
    from the prior-draft reference could smuggle a stale 'accepted' onto
    redrafted content that then survives the splice/reset."""
    from lib.topics.proposals._common import _REGENERATE_RESET_TOPIC_FIELDS
    for topic in topics:
        if isinstance(topic, dict):
            for field in _REGENERATE_RESET_TOPIC_FIELDS:
                topic.pop(field, None)


def _normalise_agent_payload(repo: Path, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    from lib.topics.proposal_drafting import (
        PROPOSAL_VERSION, ProposalValidationError, validate_proposal,
    )

    if payload.get("version") == PROPOSAL_VERSION and isinstance(payload.get("topics"), list):
        proposal = payload
    else:
        proposal = {
            "version": PROPOSAL_VERSION,
            "repo": repo.resolve().name,
            "scope": "all",
            "generated_at": utc_now(),
            "status": "draft",
            "topics": payload.get("topics") or [],
            "notes": payload.get("notes") or [],
        }
        # Carry the legacy top-level wiki through the wrap so the
        # require_wiki check sees the same fallback _combined_proposal_wiki
        # reads (pass-through payloads keep the key naturally).
        if "wiki" in payload:
            proposal["wiki"] = payload["wiki"]
    _strip_review_markers(proposal.get("topics") or [])
    wiki = _combined_proposal_wiki(payload, proposal.get("topics") or [])
    errors = validate_proposal(proposal, require_wiki=True)
    if errors:
        raise ProposalValidationError(errors)
    return proposal, wiki


def _regenerate_scope_topic_ids(out_dir: Path) -> list[str]:
    """The drifted topic ids a scoped content-drift regenerate must re-derive,
    read from the run status (set by `start_external_regenerate_run`). Empty on
    a fresh run or a full manual regenerate."""
    status = load_status(out_dir) or {}
    scope = status.get("regenerate_drift_scope") or {}
    ids = scope.get("topic_ids") or []
    return [tid for tid in ids if isinstance(tid, str) and tid]


def _pick_topic(
    topic: dict[str, Any], scope: set[str], drafted_by_id: dict[str, Any],
) -> dict[str, Any]:
    """The freshly drafted body for a scoped (drifted) topic, else the prior
    topic verbatim — so untouched pages stay byte-identical."""
    topic_id = topic.get("id")
    if topic_id in scope and topic_id in drafted_by_id:
        return drafted_by_id[topic_id]
    return topic


def _rebuild_combined_wiki(prior_wiki: str, merged_topics: list[dict[str, Any]]) -> str:
    """Prior intro + one `## ` section per (merged) per-topic wiki."""
    from lib.topics.wiki_sections import split_wiki_sections

    intro, _ = split_wiki_sections(prior_wiki)
    sections = [s for s in (_topic_wiki_section(t) for t in merged_topics) if s]
    parts = ([intro] if intro else []) + sections
    return "\n\n".join(parts).strip()


def _splice_scoped_topics(
    prior: dict[str, Any], drafted: dict[str, Any], prior_wiki: str,
    scope_topic_ids: list[str],
) -> tuple[dict[str, Any], str]:
    """Merge a scoped redraft back onto the prior full topic list.

    Only the scoped (drifted) topics take the freshly drafted body; every other
    topic is copied verbatim from the prior revision, so non-drifted wiki pages
    are byte-identical across the regenerate. The combined wiki is rebuilt from
    the prior intro plus one `## ` section per (merged) topic — keeping the full
    topic set means per-doc apply never loses a forward edge."""
    scope = set(scope_topic_ids)
    drafted_by_id = {
        t.get("id"): t for t in drafted.get("topics") or [] if t.get("id")
    }
    merged_topics = [_pick_topic(t, scope, drafted_by_id) for t in prior.get("topics") or []]
    merged = {**prior, "topics": merged_topics}
    # Drop the prior revision's stamped fields so the appended `regenerated`
    # revision is stamped fresh (`_append_new_revision` reuses a carried-over
    # `generated_at`); the inert `revision`/`revisions` keys would otherwise
    # ride along too.
    for stale_key in ("generated_at", "revision", "revisions"):
        merged.pop(stale_key, None)
    base_wiki = prior_wiki or str(prior.get("wiki") or "")
    merged_wiki = _rebuild_combined_wiki(base_wiki, merged_topics)
    return merged, (merged_wiki or base_wiki)


def _apply_regenerate_scope(
    repo: Path, out_dir: Path, proposal: dict[str, Any], wiki: str,
) -> tuple[dict[str, Any], str]:
    """If this run is a scoped content-drift regenerate, splice the drifted
    topics over the prior revision so untouched wikis stay byte-identical.

    A no-op (returns the drafted proposal unchanged) when the run isn't scoped,
    or when there's no prior revision to splice against (a fresh run). Called
    from BOTH ingest paths — the runner exit and `proposal-finish` — since the
    agent may self-ingest in its own process; the status-persisted scope is the
    shared input across that boundary."""
    scope_topic_ids = _regenerate_scope_topic_ids(out_dir)
    if not scope_topic_ids:
        return proposal, wiki
    from lib.topics.proposals.core_io import load_proposal

    try:
        prior = load_proposal(repo, out_dir.name)
    except Exception:  # noqa: BLE001 — best-effort; fall back to full redraft
        return proposal, wiki
    if not prior or not (prior.get("topics") or []):
        return proposal, wiki
    prior_wiki_path = out_dir / "wiki.md"
    prior_wiki = prior_wiki_path.read_text() if prior_wiki_path.exists() else ""
    return _splice_scoped_topics(prior, proposal, prior_wiki, scope_topic_ids)


def _topic_claimed_paths(topic: dict[str, Any]) -> set[str]:
    paths = set(path for path in topic.get("evidence_paths", []) if isinstance(path, str))
    for ref in topic.get("refs", []):
        if isinstance(ref, dict) and isinstance(ref.get("path"), str):
            paths.add(ref["path"])
    return paths


def _validate_paths(repo: Path, proposal: dict[str, Any]) -> None:
    # No evidence pack: every proposed path must exist in the working tree
    # and stay inside the repo. Stricter than the old evidence-or-repo check.
    repo_root = repo.resolve()
    for topic in proposal.get("topics", []):
        for path in _topic_claimed_paths(topic):
            resolved = (repo / path).resolve()
            if repo_root not in resolved.parents and resolved != repo_root:
                raise ValueError(f"path escapes repository: {path}")
            if not resolved.exists():
                raise ValueError(f"path not found in repository: {path}")


def _read_topic_signature(repo: Path) -> str | None:
    # Structural fingerprint of the approved topic graph that ignores the
    # `updated_at` field (rewritten on every save_graph) and any whitespace
    # / key-order changes. Only flips when a topic, edge, or alias actually
    # changed — so a concurrent no-op save no longer trips the integrity check.
    # Reads through load_graph so the split dir is fingerprinted.
    from lib.topics.core import load_graph
    try:
        graph = load_graph(repo)
    except (TopicGraphError, OSError, json.JSONDecodeError):
        return None
    if isinstance(graph, dict):
        graph = {k: v for k, v in graph.items() if k != "updated_at"}
    return json.dumps(graph, sort_keys=True, separators=(",", ":"))
