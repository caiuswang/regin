"""Flask-free domain logic behind the proposal /diff + /apply endpoints.

Cross-store contract: apply always recomputes the diff server-side from
the caller's (strategy, target_topic_id, options). The caller NEVER
supplies the diff itself — otherwise a diff at T0 followed by an apply
at T1 commits a stale prospective_graph that doesn't reflect
intervening accepts.

Error contract for both entry points: LookupError when the proposal
can't be loaded; TopicGraphError/ValueError for invalid strategy, diff
composition, and the ready-gate. HTTP adapters map LookupError → 404
and the rest → 400.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import TopicAudit
from lib.topics.apply import (
    ApplyOptions,
    apply_diff,
    resolve_diff_with_options,
)
from lib.topics.core import TopicGraphError
from lib.topics.diff import (
    VALID_STRATEGIES,
    diff_against_graph,
    serialize_diff,
    serialize_issue,
)
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.snapshots import resolve_or_create_repo

from ._common import _find_proposed_topic
from .core_io import load_proposal, save_proposal
from .topic_actions import _VALID_EDGE_TYPES, _approved_topic_from_proposal


# ── Shared helpers ──────────────────────────────────────────────────


def _validate_strategy(strategy: str) -> None:
    if strategy not in VALID_STRATEGIES:
        raise TopicGraphError(
            f"strategy must be one of {list(VALID_STRATEGIES)}"
        )


def _apply_options_from_dict(raw: Any) -> ApplyOptions:
    """Build ApplyOptions from a plain dict, falling back to defaults.

    `raw` is optional; when absent we use the library defaults
    (`prune_orphan_edges=True`, others False). When present, only known
    flags are honoured — extra keys are ignored rather than rejected so
    the frontend can ship ahead of the backend.
    """
    if not isinstance(raw, dict):
        return ApplyOptions()
    defaults = ApplyOptions()
    return ApplyOptions(
        prune_orphan_edges=bool(raw.get("prune_orphan_edges", defaults.prune_orphan_edges)),
        drop_dead_refs=bool(raw.get("drop_dead_refs", defaults.drop_dead_refs)),
        dedupe_aliases=bool(raw.get("dedupe_aliases", defaults.dedupe_aliases)),
    )


def _resolve_proposed_topic(
    repo_path: str, proposal_id: str, proposed_topic_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | tuple[None, None, None]:
    """Load proposal + locate the proposed topic + compose its approved shape.

    Returns `(proposal, proposed_topic, approved_shape)`. The "approved
    shape" is the topic dict as it would land in the graph (uses the
    same `_approved_topic_from_proposal` helper as the legacy shim, so
    orphan-edge prefiltering matches legacy behaviour at this layer).
    """
    try:
        proposal = load_proposal(repo_path, proposal_id)
    except (OSError, TopicGraphError):
        return None, None, None
    proposed = _find_proposed_topic(proposal, proposed_topic_id)
    graph = load_authoritative_graph(repo_path)
    sibling_ids = set(graph.get("topics", {}).keys())
    approved = _approved_topic_from_proposal(proposed, existing_topic_ids=sibling_ids)
    approved["id"] = proposed.get("id") or proposed_topic_id
    return proposal, proposed, approved


def _existing_apply_snapshot(
    repo_id: int, proposal_id: str, approved_id: str,
) -> int | None:
    """Return the snapshot_id of a prior apply for (proposal, approved_id).

    Idempotency check for apply: covers the crash window between
    `apply_diff` committing the SQL+disk write and the legacy
    `save_proposal` update. Without this, a UI that re-clicks Apply
    after a partial-write crash would hit "topic already exists" 400.

    Queries TopicAudit provenance rows (always written inside the same
    transaction as the snapshot) rather than reading the snapshot's
    diff_summary_json — provenance rows are indexed on
    `triggering_run_id` so the lookup is cheap.
    """
    with SessionLocal() as s:
        row = s.exec(
            select(TopicAudit)
            .where(TopicAudit.repo_id == repo_id)
            .where(TopicAudit.triggering_run_id == proposal_id)
            .where(TopicAudit.kind == "provenance")
            .where(TopicAudit.topic_ids_json.like(f'%"{approved_id}"%'))
        ).first()
        return row.snapshot_id if row is not None else None


def _build_resolved_diff(
    repo_path: str,
    approved: dict[str, Any],
    *,
    strategy: str,
    target_topic_id: str | None,
    options: ApplyOptions,
):
    """Compose `(raw_diff, resolved_diff, dropped_items)` for one request.

    Centralised so diff and apply share the exact same composition —
    apply MUST NOT trust a client-sent diff (advisor pin #2).
    """
    graph = load_authoritative_graph(repo_path)
    raw_diff = diff_against_graph(
        approved, graph,
        strategy=strategy,
        target_topic_id=target_topic_id,
        repo_path=repo_path,
    )
    resolved_diff, dropped = resolve_diff_with_options(
        raw_diff, options, repo_path=repo_path,
    )
    return raw_diff, resolved_diff, dropped


# ── diff (side-effect-free) ─────────────────────────────────────────


def diff_proposal_topic(
    repo_path: str,
    proposal_id: str,
    proposed_topic_id: str,
    *,
    strategy: str = "create",
    target_topic_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute the prospective diff for a (proposal, topic, strategy).

    Side-effect-free. Returns the resolved diff + the items that would
    be silently dropped under the supplied (or default) options.
    """
    _validate_strategy(strategy)
    opts = _apply_options_from_dict(options)
    proposal, _proposed, approved = _resolve_proposed_topic(
        repo_path, proposal_id, proposed_topic_id,
    )
    if proposal is None:
        raise LookupError(f"proposal not found: {proposal_id}")
    raw, resolved, dropped = _build_resolved_diff(
        repo_path, approved,
        strategy=strategy, target_topic_id=target_topic_id, options=opts,
    )
    return {
        "diff": serialize_diff(resolved),
        "dropped_items": dropped.to_json(),
        # Echo what we'd get without resolution so the UI can show
        # "here's what we silently filtered" diffs against the raw view.
        "raw_introduced_errors": [serialize_issue(e) for e in raw.introduced_errors],
    }


# ── apply (commits) ─────────────────────────────────────────────────


def _restore_pruned_inbound_edges_after_apply(
    repo_path: str, proposal: dict, resolved,
) -> None:
    """Re-attach edges that downgrade had to prune from sibling topics.

    Reads `proposal.metadata.pruned_inbound_edges[applied_topic_id]`
    and patches the matching siblings in the live graph. Idempotent:
    only adds an edge if no edge with the same target+type is already
    present. Silent no-op when the metadata is absent or the applied
    delta is empty.
    """
    if not resolved.topic_deltas:
        return
    metadata = proposal.get("metadata") or {}
    bucket = metadata.get("pruned_inbound_edges") or {}
    applied_topic_id = resolved.topic_deltas[0].topic_id
    pruned = bucket.get(applied_topic_id)
    if not pruned:
        return
    from lib.topics.graph_io import export_overlay_to_disk
    from lib.topics.proposals import _restore_pruned_edges
    graph = load_authoritative_graph(repo_path)
    _restore_pruned_edges(graph.get("topics", {}), pruned)
    # Route the edge-restore to the overlay; base topic.json stays clean.
    export_overlay_to_disk(repo_path, graph)


def _forward_sibling_edge(
    edge: Any, *, sibling_ids: set[str], live_ids: set[str],
) -> tuple[str, str] | None:
    """Return `(edge_type, target)` iff `edge` is a forward reference to a
    not-yet-applied sibling in the same proposal; else None."""
    if not isinstance(edge, dict):
        return None
    target = edge.get("target") or edge.get("to")
    edge_type = edge.get("type") or edge.get("rel") or "related"
    if not isinstance(target, str) or edge_type not in _VALID_EDGE_TYPES:
        return None
    if target in live_ids or target not in sibling_ids:
        return None
    return edge_type, target


def _stage_forward_sibling_edges(
    repo_path: str, proposal: dict, proposed: dict, approved: dict,
    *, strategy: str,
) -> None:
    """Stash edges from the just-applied topic to siblings in the SAME
    proposal that aren't in the approved graph yet.

    A multi-doc proposal can have doc1 reference doc3. If doc1 is applied
    first, `_approved_topic_from_proposal` prunes the doc1→doc3 edge (its
    target isn't in the live graph) so the apply stays valid — but the
    edge is then lost forever, because applying doc3 later never revisits
    doc1. We record the dropped edge into
    `proposal.metadata.pruned_inbound_edges[doc3] = {doc1: [edge]}`,
    reusing the exact format + restore path the downgrade round-trip uses
    (`_restore_pruned_inbound_edges_after_apply` re-attaches it onto doc1
    once doc3 lands). Idempotent: never records a duplicate.

    Scoped to create/replace — for those the applied topic's graph id is
    `approved["id"]`; merge folds edges into a different target id.
    """
    if strategy not in ("create", "replace"):
        return
    source_id = approved["id"]
    sibling_ids = {
        t.get("id") for t in proposal.get("topics", [])
        if isinstance(t, dict) and t.get("id") and t.get("id") != source_id
    }
    if not sibling_ids:
        return
    live_ids = set(load_authoritative_graph(repo_path).get("topics", {}).keys())
    bucket = proposal.setdefault("metadata", {}).setdefault("pruned_inbound_edges", {})
    for edge in proposed.get("edges") or []:
        forward = _forward_sibling_edge(
            edge, sibling_ids=sibling_ids, live_ids=live_ids,
        )
        if forward is None:
            continue
        edge_type, target = forward
        edges = bucket.setdefault(target, {}).setdefault(source_id, [])
        _append_edge_once(edges, edge_type=edge_type, target=target)


def _append_edge_once(
    edges: list[dict], *, edge_type: str, target: str,
) -> None:
    """Append `{type, target}` to `edges` unless an equal edge is present."""
    for existing in edges:
        if existing.get("target") == target and existing.get("type") == edge_type:
            return
    edges.append({"type": edge_type, "target": target})


def _advance_drift_baseline_after_apply(
    repo_path: str, resolved, *, strategy: str,
) -> None:
    """Re-run the legacy accept/replace/merge shims' post-apply hooks on the
    modern apply path.

    The Phase-C UI applies exclusively through this path, which commits via
    `apply_diff` directly — bypassing the `accept_proposed_topic` /
    `replace_approved_topic` shims that re-fingerprint a topic's refs
    (`_capture_ref_digests_on_accept`) and un-stale its drift-demoted memories
    (`_restore_topic_memories_on_accept`). Without re-running them here,
    applying a content-drift *refresh* leaves `TopicRefDigest.content_hash`
    stale, so the very next `regin topics evolve` re-detects the same drift —
    forever.

    Both helpers are gated on `topic_evolution.evolution_enabled` (off by
    default → no-op) and best-effort (never raise), so this is invisible on the
    default config. Strategy mirrors the legacy shims exactly: create/replace
    capture + restore; merge restores only (it never captured)."""
    if not resolved.topic_deltas:
        return
    from lib.topics.proposals.topic_actions import (
        _capture_ref_digests_on_accept,
        _restore_topic_memories_on_accept,
    )
    applied_id = resolved.topic_deltas[0].topic_id
    if strategy in ("create", "replace"):
        _capture_ref_digests_on_accept(repo_path, applied_id)
    _restore_topic_memories_on_accept(applied_id)
    # Applying a refresh resolves the drift → clear its inbox card (no-op
    # for a non-drift apply, which has no live drift card under this key).
    from lib.topics.content_drift import resolve_drift_card
    resolve_drift_card(repo_path, applied_id)


def _already_applied_noop_snapshot(
    repo_id: int,
    proposal_id: str,
    resolved,
    repo_path: str,
    *,
    strategy: str,
    target: str | None,
    approved: dict,
) -> int | None:
    """Prior snapshot_id iff re-applying this (proposal, topic) is a no-op.

    A prior apply may have committed its snapshot before `save_proposal`
    updated review_status (crash window), so a re-click must not 400. But
    we only short-circuit when the live topic already equals what this
    diff would write: a regenerated draft has different content and must
    run the real apply. A topic downgraded out of the graph has no live
    entry, so `live_topic is None` and we re-apply there too.
    """
    idempotent_target = target if strategy == "merge" else approved["id"]
    prior_snap_id = _existing_apply_snapshot(repo_id, proposal_id, idempotent_target)
    if prior_snap_id is None or resolved.prospective_graph is None:
        return None
    live_graph = load_authoritative_graph(repo_path)
    live_topic = live_graph.get("topics", {}).get(idempotent_target)
    prospective_topic = resolved.prospective_graph.get("topics", {}).get(idempotent_target)
    if live_topic is not None and live_topic == prospective_topic:
        return prior_snap_id
    return None


def _review_state_not_ready(proposal: dict) -> bool:
    """True when the proposal isn't in an apply-eligible review state."""
    review_state = (
        proposal.get("status")
        or proposal.get("metadata", {}).get("proposal_status")
        or "draft"
    )
    return review_state not in {"ready_to_apply", "partially_applied"}


def _wiki_pages_for_apply(
    repo_path: str, proposal_id: str, strategy: str, resolved, proposed: dict,
) -> dict[str, str] | None:
    """create/replace write the applied topic's OWN wiki page to
    .regin/topics/wiki/<id>.md. The proposed topic carries its own `wiki`
    (per-topic drafting); we fall back to the run's combined wiki.md only for
    legacy proposals with no per-topic body. merge keeps the target topic's
    existing wiki, so it gets no wiki_pages entry."""
    if strategy not in ("create", "replace") or not resolved.topic_deltas:
        return None
    body = str((proposed or {}).get("wiki") or "").strip()
    if not body:
        from lib.topics.core import topic_dir as _topic_dir
        proposal_wiki = _topic_dir(repo_path) / "proposals" / proposal_id / "wiki.md"
        if not proposal_wiki.exists():
            return None
        body = proposal_wiki.read_text()
    return {resolved.topic_deltas[0].topic_id: body}


def _mark_proposal_topic_applied(
    proposal: dict, proposed: dict, approved: dict, resolved,
    *, strategy: str, target: str | None,
) -> None:
    """Mirror the legacy review_status bookkeeping on the proposal dict so
    the existing UI panels keep working. Phase D removes this."""
    from lib.topics import utc_now
    now = utc_now()
    applied_id = resolved.topic_deltas[0].topic_id if resolved.topic_deltas else approved["id"]
    if strategy in ("create", "replace"):
        proposed["review_status"] = "accepted"
        proposed["accepted_topic"] = applied_id
        proposed["accepted_at"] = now
        if strategy == "replace":
            proposed["replaced_existing"] = True
    elif strategy == "merge":
        proposed["review_status"] = "merged"
        proposed["merged_topic"] = target
        proposed["merged_at"] = now
    proposal["status"] = (
        "applied"
        if all(t.get("review_status") for t in proposal.get("topics", []))
        else "partially_applied"
    )


def _advance_noop_review_markers(
    repo_path: str, proposal_id: str, proposal: dict, proposed: dict,
    approved: dict, resolved, *, strategy: str, target: str | None,
) -> None:
    """A no-op re-apply leaves the graph untouched, but the proposal side
    may still say pending — a regenerate whose redraft came back
    byte-identical (or a crash between apply_diff and save_proposal)
    re-enters the short-circuit on every click, wedging the run at
    partially_applied. Advance the review markers and the drift baseline;
    edge restore/staging stays with the real apply path.

    Deliberately narrower than a real apply: create/replace only (a no-op
    merge can match a SIBLING topic's provenance row via
    _existing_apply_snapshot and would misattribute it as merged), only in
    review states a real apply accepts, and never while a regenerate is in
    flight — save_proposal rewrites the latest revision in place and would
    clobber the incoming redraft."""
    if proposed.get("review_status") not in (None, "", "pending"):
        return
    if strategy not in ("create", "replace") or _review_state_not_ready(proposal):
        return
    from lib.topics.proposals._common import _guard_regenerate_not_in_flight
    try:
        _guard_regenerate_not_in_flight(repo_path, proposal_id)
    except TopicGraphError:
        return
    _mark_proposal_topic_applied(
        proposal, proposed, approved, resolved, strategy=strategy, target=target,
    )
    save_proposal(repo_path, proposal_id, proposal)
    _advance_drift_baseline_after_apply(repo_path, resolved, strategy=strategy)


def apply_proposal_topic(
    repo_path: str,
    proposal_id: str,
    proposed_topic_id: str,
    *,
    strategy: str = "create",
    target_topic_id: str | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve + apply a proposed topic atomically.

    Returns:
      `{ok: True, snapshot_id, applied_diff, dropped_items}` on success;
      `{ok: True, already_applied: True, snapshot_id}` on a no-op re-apply;
      `{ok: False, error: "unresolvable_errors", diff, dropped_items}` when
      the post-resolution diff is still not applyable — the caller re-renders
      the unresolved errors so the user can toggle more options.
    """
    _validate_strategy(strategy)
    opts = _apply_options_from_dict(options)
    proposal, proposed, approved = _resolve_proposed_topic(
        repo_path, proposal_id, proposed_topic_id,
    )
    if proposal is None:
        raise LookupError(f"proposal not found: {proposal_id}")

    repo = resolve_or_create_repo(repo_path)
    _raw, resolved, dropped = _build_resolved_diff(
        repo_path, approved,
        strategy=strategy, target_topic_id=target_topic_id, options=opts,
    )

    # Idempotency: a prior apply may have committed before save_proposal
    # ran (crash window), so re-clicking Apply must not 400. We only
    # short-circuit on a true no-op — see _already_applied_noop_snapshot,
    # which gates on the live topic matching what we'd write so a
    # regenerated draft still applies.
    prior_snap_id = _already_applied_noop_snapshot(
        repo.id, proposal_id, resolved, repo_path,
        strategy=strategy, target=target_topic_id, approved=approved,
    )
    if prior_snap_id is not None:
        _advance_noop_review_markers(
            repo_path, proposal_id, proposal, proposed, approved, resolved,
            strategy=strategy, target=target_topic_id,
        )
        return {
            "ok": True,
            "already_applied": True,
            "snapshot_id": prior_snap_id,
        }

    if _review_state_not_ready(proposal):
        raise TopicGraphError("proposal must be marked ready before apply")

    if not resolved.is_applyable:
        return {
            "ok": False,
            "error": "unresolvable_errors",
            "diff": serialize_diff(resolved),
            "dropped_items": dropped.to_json(),
        }

    result = apply_diff(
        repo.id, resolved,
        options=opts,
        reason=strategy,
        triggering_run_id=proposal_id,
        wiki_pages=_wiki_pages_for_apply(repo_path, proposal_id, strategy, resolved, proposed),
    )

    # Round-trip the edges that downgrade had to prune: if this
    # proposal's run metadata recorded inbound edges that were dropped
    # to make the downgrade legal, patch them back into the siblings
    # now that the topic is in the graph again.
    _restore_pruned_inbound_edges_after_apply(
        repo_path, proposal, resolved,
    )

    # Multi-doc proposal forward refs: if this topic edges to a sibling
    # that hasn't been applied yet, the prune dropped that edge to keep
    # the apply valid. Stash it so applying the sibling later re-attaches
    # it (same machinery as the downgrade round-trip above). Without this,
    # approving docs one at a time silently loses the inter-doc edges.
    _stage_forward_sibling_edges(
        repo_path, proposal, proposed, approved, strategy=strategy,
    )

    # Advance the content-drift baseline (and recover drift-demoted memories)
    # for the just-applied topic. The legacy accept/replace/merge shims did
    # this; the modern apply_diff path doesn't, so without it a refreshed
    # topic stays "drifted" on every subsequent `regin topics evolve`.
    _advance_drift_baseline_after_apply(repo_path, resolved, strategy=strategy)

    _mark_proposal_topic_applied(
        proposal, proposed, approved, resolved, strategy=strategy, target=target_topic_id,
    )
    save_proposal(repo_path, proposal_id, proposal)

    return {
        "ok": True,
        "snapshot_id": result.snapshot_id,
        "applied_diff": serialize_diff(resolved),
        "dropped_items": dropped.to_json(),
    }
