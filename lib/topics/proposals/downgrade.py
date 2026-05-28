"""Downgrade: lift an approved topic back into a proposal draft.

Two paths, tried in order:

  1. **Merge-into-origin**: append a new `kind='downgraded'` revision
     onto the proposal run that originally brought this topic into the
     approved graph. Keeps the whole lifecycle in one run.
  2. **Fresh proposal**: when there's no provenance pointer (legacy
     snapshots predate `triggering_run_id`) or the origin run was
     deleted, create a brand-new `approved-topic-downgrade` proposal
     run instead.

Both paths drop the topic + its inbound edges from the approved graph
(rolling back atomically on failure), unaccept any stale proposal-row
accept markers pointing at the topic, persist the topic's wiki into
`proposal_dir/wiki.md` so re-apply has somewhere to read it from, and
schedule the wiki PatternDoc reconcile in a background thread.
"""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from lib.topics import TopicGraphError, slugify, topic_dir, utc_now, validate
from lib.topics.graph_io import export_overlay_to_disk, load_authoritative_graph
from lib.topics.proposal_drafting import PROPOSAL_VERSION, validate_proposal

from ._common import _reindex_wiki_after_graph_change, _topics_log
from .core_io import save_proposal


def _build_downgrade_topic_payload(
    topic_id: str, approved: dict[str, Any], downgraded_at: str,
) -> dict[str, Any]:
    return {
        "id": topic_id,
        "label": approved["label"],
        "aliases": list(approved.get("aliases", [])),
        "intent": approved["intent"],
        "status": approved.get("status", "active"),
        "refs": deepcopy(approved.get("refs", [])),
        "edges": deepcopy(approved.get("edges", [])),
        "commands": list(approved.get("commands", [])),
        "include_globs": list(approved.get("include_globs", [])),
        "exclude_globs": list(approved.get("exclude_globs", [])),
        "evidence_paths": [
            ref.get("path") for ref in approved.get("refs", [])
            if isinstance(ref, dict) and ref.get("path")
        ],
        "review_status": "pending",
        "downgraded_from": topic_id,
        "downgraded_at": downgraded_at,
    }


def _resolve_downgrade_wiki_content(
    repo: Path, proposal_dir: Path, topic_id: str, approved_label: str,
) -> str:
    """Pick the right wiki body for a downgraded topic. Approved per-topic
    wiki wins; otherwise fall back to the proposal_dir wiki or a stub.
    """
    approved_wiki = topic_dir(repo) / "wiki" / f"{slugify(topic_id)}.md"
    wiki_path = proposal_dir / "wiki.md"
    if approved_wiki.exists():
        return approved_wiki.read_text()
    if wiki_path.exists():
        return wiki_path.read_text()
    return (
        f"# {approved_label or topic_id}\n\n"
        f"Downgraded from approved topic `{topic_id}`. "
        "Original wiki narrative was not available; edit this draft before re-approving.\n"
    )


def _restore_pruned_edges(
    topics_map: dict[str, Any],
    pruned: dict[str, list[dict[str, Any]]],
) -> None:
    """Re-attach dropped inbound edges to siblings — invoked from rollback
    paths AND from apply when restoring a downgraded topic.

    Idempotent: only adds an edge if no edge with the same target+type
    is already present.
    """
    for sibling_id, edges in pruned.items():
        sibling = topics_map.get(sibling_id)
        if sibling is None:
            continue
        existing = sibling.get("edges") or []
        seen = {(e.get("target"), e.get("type")) for e in existing}
        for edge in edges:
            key = (edge.get("target"), edge.get("type"))
            if key in seen:
                continue
            existing.append(edge)
            seen.add(key)
        sibling["edges"] = existing


def _drop_topic_from_approved_graph(
    repo: Path, graph: dict, topic_id: str,
) -> tuple[Any, dict[str, list[dict[str, Any]]]]:
    """Remove the topic from the approved graph + validate. Returns
    `(removed_topic, pruned_inbound_edges)` so the caller can roll back
    on failure AND persist the pruned edges in the proposal metadata
    for round-trip restoration on re-apply.

    `pruned_inbound_edges` is `{sibling_topic_id: [edge_dict, ...]}` —
    only edges whose target was `topic_id` (the ones we had to drop to
    make validate() pass). Leaving them in place would fail with
    `edge_target_missing` and block the downgrade. The Apply panel
    surfaces the same fix as the `prune_orphan_edges` resolution
    checkbox; for downgrade we always want it.
    """
    topics = graph.get("topics", {})
    removed = topics.pop(topic_id)
    pruned: dict[str, list[dict[str, Any]]] = {}
    original_edges: dict[str, list[dict[str, Any]]] = {}
    for sibling_id, sibling in topics.items():
        edges = sibling.get("edges") or []
        kept = [e for e in edges if e.get("target") != topic_id]
        dropped = [e for e in edges if e.get("target") == topic_id]
        if dropped:
            original_edges[sibling_id] = edges
            pruned[sibling_id] = dropped
            sibling["edges"] = kept
    export_overlay_to_disk(repo, graph)
    result = validate(repo)
    if not result.ok:
        topics[topic_id] = removed
        for sibling_id, original in original_edges.items():
            topics[sibling_id]["edges"] = original
        export_overlay_to_disk(repo, graph)
        raise TopicGraphError("; ".join(result.errors))
    return removed, pruned


# ───────────────────── merge-into-origin path ──────────────────────────


def _downgrade_into_origin_run(
    repo: Path,
    origin_run_id: str,
    topic_id: str,
    approved: dict[str, Any],
    downgraded_at: str,
    pruned_inbound_edges: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Try to append a new revision (kind='downgraded') onto the origin
    proposal run that last brought this topic into the approved graph.
    Returns the response dict on success, None if the merge couldn't
    happen (origin missing / latest revision gone) so the caller can
    fall through to the legacy "fresh proposal" path.
    """
    from lib.topics.proposal_orm import orm_append_downgrade_revision

    proposal_topic = _build_downgrade_topic_payload(topic_id, approved, downgraded_at)
    origin_dir = topic_dir(repo) / "proposals" / origin_run_id
    origin_dir.mkdir(parents=True, exist_ok=True)
    wiki_content = _resolve_downgrade_wiki_content(
        repo, origin_dir, topic_id, approved.get("label") or "",
    )

    proposal = orm_append_downgrade_revision(
        repo, origin_run_id, topic_id,
        proposal_topic, wiki_content, downgraded_at,
        pruned_inbound_edges=pruned_inbound_edges,
    )
    if proposal is None:
        return None

    # Mirror restore: the apply path reads wiki from disk, so keep the
    # proposal_dir wiki.md in sync with the new revision's body.
    (origin_dir / "wiki.md").write_text(wiki_content)
    return {
        "id": origin_run_id,
        "proposal": proposal,
        "topic": proposal_topic,
        "revision_id": proposal.get("revision", {}).get("id"),
        "merged_into_origin": True,
    }


def _try_downgrade_into_origin(
    repo: Path,
    graph: dict[str, Any],
    topic_id: str,
    approved: dict[str, Any],
) -> dict[str, Any] | None:
    """Attempt the merge-into-origin path. Returns the response dict on
    success, None if no origin or the merge couldn't complete (so the
    caller falls through to the legacy "fresh proposal" path)."""
    from lib.topics.proposal_orm import (
        orm_find_origin_proposal_run_for_topic,
        orm_unaccept_topic_across_proposals,
    )

    origin_run_id = orm_find_origin_proposal_run_for_topic(repo, topic_id)
    if origin_run_id is None:
        return None
    downgraded_at = utc_now()
    topics_map = graph.get("topics", {})
    removed, pruned_edges = _drop_topic_from_approved_graph(repo, graph, topic_id)
    orm_unaccept_topic_across_proposals(repo, topic_id)
    try:
        merged = _downgrade_into_origin_run(
            repo, origin_run_id, topic_id, approved, downgraded_at,
            pruned_inbound_edges=pruned_edges,
        )
    except Exception:
        topics_map[topic_id] = removed
        _restore_pruned_edges(topics_map, pruned_edges)
        export_overlay_to_disk(repo, graph)
        raise
    if merged is None:
        # Origin vanished between lookup and append. Restore the graph
        # so the legacy fallback can try again with a fresh proposal.
        topics_map[topic_id] = removed
        _restore_pruned_edges(topics_map, pruned_edges)
        export_overlay_to_disk(repo, graph)
        return None
    _topics_log().write(
        "topic_downgraded_to_draft",
        topic_id=topic_id,
        proposal_id=origin_run_id,
        repo_path=str(repo),
        label=approved.get("label"),
        merged_into_origin=True,
    )
    _reindex_wiki_after_graph_change(repo)
    return merged


# ───────────────────── fresh-proposal path ────────────────────────────


def _make_fresh_proposal_dir(repo: Path, topic_id: str) -> tuple[str, Path]:
    proposal_id = utc_now().replace(":", "").replace("-", "")
    proposal_dir = topic_dir(repo) / "proposals" / proposal_id
    try:
        proposal_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        # Concurrent downgrade attempts within the same wall-clock second
        # collide on the timestamp-derived id. The earlier attempt has
        # already run (or is mid-flight); surface that as a 400 instead
        # of a 500 so the UI can prompt the user to refresh.
        raise TopicGraphError(
            f"downgrade already in progress for {topic_id} "
            f"(proposal {proposal_id}); refresh to see the new draft"
        ) from exc
    return proposal_id, proposal_dir


def _build_fresh_evidence_pack(
    repo_name: str, proposal_topic: dict[str, Any], downgraded_at: str, topic_id: str,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "repo": repo_name,
        "scope": "approved-topic",
        "generated_at": downgraded_at,
        "summary": {
            "file_count": len(proposal_topic["evidence_paths"]),
            "top_directories": {},
        },
        "existing_topics": [],
        "topic_request": f"Downgraded approved topic {topic_id}",
    }
    if proposal_topic["evidence_paths"]:
        top_dirs: dict[str, int] = {}
        for path in proposal_topic["evidence_paths"]:
            head = Path(path).parts[0] if Path(path).parts else "."
            top_dirs[head] = top_dirs.get(head, 0) + 1
        evidence["summary"]["top_directories"] = top_dirs
    return evidence


def _build_fresh_downgrade_proposal(
    repo_name: str, topic_id: str, downgraded_at: str, proposal_topic: dict[str, Any],
) -> dict[str, Any]:
    proposal: dict[str, Any] = {
        "version": PROPOSAL_VERSION,
        "repo": repo_name,
        "scope": "approved-topic",
        "generated_at": downgraded_at,
        "status": "draft",
        "provider": "approved-topic-downgrade",
        "notes": [
            f"Draft created from approved topic `{topic_id}`.",
            "Review or delete this proposal before re-approving it.",
        ],
        "topics": [proposal_topic],
    }
    errors = validate_proposal(proposal)
    if errors:
        raise TopicGraphError("; ".join(errors))
    return proposal


def _persist_fresh_downgrade(
    repo: Path,
    proposal_id: str,
    proposal_dir: Path,
    proposal: dict[str, Any],
    evidence: dict[str, Any],
    topic_id: str,
    approved_label: str,
) -> None:
    """Write evidence + wiki to disk, populate proposal['wiki'], save to ORM.

    Resolve wiki content BEFORE save_proposal so the ORM revision row
    gets `wiki_md` populated — otherwise the disk wiki.md is fine but
    the workspace falls back to an empty ORM payload.
    """
    (proposal_dir / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    )
    wiki_content = _resolve_downgrade_wiki_content(
        repo, proposal_dir, topic_id, approved_label,
    )
    (proposal_dir / "wiki.md").write_text(wiki_content)
    proposal["wiki"] = wiki_content
    save_proposal(repo, proposal_id, proposal)


def _downgrade_via_fresh_proposal(
    repo: Path,
    graph: dict[str, Any],
    topic_id: str,
    approved: dict[str, Any],
) -> dict[str, Any]:
    """Legacy fallback: create a brand-new proposal run for the
    downgraded topic when no origin run can be found."""
    from lib.topics.proposal_orm import orm_unaccept_topic_across_proposals

    proposal_id, proposal_dir = _make_fresh_proposal_dir(repo, topic_id)
    downgraded_at = utc_now()
    proposal_topic = _build_downgrade_topic_payload(topic_id, approved, downgraded_at)
    proposal = _build_fresh_downgrade_proposal(
        repo.name, topic_id, downgraded_at, proposal_topic,
    )
    evidence = _build_fresh_evidence_pack(
        repo.name, proposal_topic, downgraded_at, topic_id,
    )

    topics = graph.get("topics", {})
    removed, pruned_edges = _drop_topic_from_approved_graph(repo, graph, topic_id)
    if pruned_edges:
        proposal.setdefault("metadata", {})["pruned_inbound_edges"] = {topic_id: pruned_edges}
    # The topic is no longer in the approved graph, so every proposal that
    # accepted into it must shed the stale `review_status='accepted'`
    # marker — otherwise the UI's Accept button stays hidden and the user
    # can't re-accept the original draft.
    orm_unaccept_topic_across_proposals(repo, topic_id)

    try:
        _persist_fresh_downgrade(
            repo, proposal_id, proposal_dir, proposal, evidence,
            topic_id, approved.get("label") or "",
        )
    except Exception:
        topics[topic_id] = removed
        _restore_pruned_edges(topics, pruned_edges)
        export_overlay_to_disk(repo, graph)
        shutil.rmtree(proposal_dir, ignore_errors=True)
        raise
    _topics_log().write(
        "topic_downgraded_to_draft",
        topic_id=topic_id,
        proposal_id=proposal_id,
        repo_path=str(repo),
        label=approved.get("label"),
    )
    # Reconcile the wiki PatternDoc index: when this topic was approved
    # it landed as `wiki/<repo>/<topic_id>` in the patterns table, and
    # the repo's /api/repos/<name> patterns table still showed it after
    # downgrade because the indexer was only re-run on apply, not on
    # downgrade. Step 5 of index_wikis deletes rows whose slug isn't
    # backed by an approved-graph topic — exactly the cleanup we need.
    _reindex_wiki_after_graph_change(repo)
    return {"id": proposal_id, "proposal": proposal, "topic": proposal_topic}


# ───────────────────── public entry point ──────────────────────────────


def downgrade_topic_to_proposal(repo_path: str | Path, topic_id: str) -> dict[str, Any]:
    # Downgrade routes through the local overlay (export_overlay_to_disk),
    # so removing the topic records a `deleted_topics` tombstone rather than
    # touching the git-tracked base topic.json. Re-applying the proposal
    # clears the tombstone (export_overlay recomputes it from scratch).
    # FOLLOW-UP: rejecting/discarding the proposal does NOT yet clear the
    # tombstone, so the topic stays masked locally until re-apply or a
    # hand-edit. Wire tombstone cleanup into the proposal reject path.
    repo = Path(repo_path)
    graph = load_authoritative_graph(repo)
    approved = graph.get("topics", {}).get(topic_id)
    if not approved:
        raise TopicGraphError(f"topic not found: {topic_id}")

    # Prefer appending the downgrade as a new revision on the proposal
    # that first applied this topic — keeps the topic's whole lifecycle
    # in one run. Falls through when there's no provenance pointer
    # (legacy snapshots) or the origin run was deleted.
    merged = _try_downgrade_into_origin(repo, graph, topic_id, approved)
    if merged is not None:
        return merged
    return _downgrade_via_fresh_proposal(repo, graph, topic_id, approved)
