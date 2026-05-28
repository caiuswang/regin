"""Phase B endpoints: /diff, /apply, /audit, /snapshots.

These sit ALONGSIDE the legacy /accept, /merge, /replace, /ignore
endpoints (in `proposals.py`); the old ones stay functional through
Phase D. Phase C's frontend rewrite migrates the UI to call /diff +
/apply exclusively; the old ones get removed in Phase E.

Cross-store contract: /apply always recomputes the diff server-side
from the request's (strategy, target_topic_id, options). The client
NEVER sends the diff itself — otherwise a /diff at T0 followed by
/apply at T1 commits a stale prospective_graph that doesn't reflect
intervening accepts.
"""

from __future__ import annotations

import json
from typing import Any

from flask import jsonify, request
from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import GraphSnapshot, Repo, TopicAudit
from lib.topics import TopicGraphError
from lib.topics.graph_io import load_authoritative_graph
from lib.topics.apply import (
    ApplyOptions,
    DroppedItems,
    apply_diff,
    resolve_diff_with_options,
)
from lib.topics.bulk_fix import AUTO_FIXABLE_CODES, compose_fix
from lib.topics.diff import (
    GraphDiff,
    VALID_STRATEGIES,
    compute_topic_delta,
    diff_against_graph,
    serialize_diff,
    serialize_issue,
)
from lib.topics.proposals import (
    _approved_topic_from_proposal,
    _find_proposed_topic,
    load_proposal,
)
from lib.topics.snapshots import (
    latest_snapshot,
    list_snapshots,
    resolve_or_create_repo,
    restore_preview,
    restore_snapshot,
)
from lib.topics.validation import audit_graph

from web.blueprints.topics import topics_bp
from web.blueprints.topics._helpers import _error, _repo_path_or_404


# ── Shared helpers ──────────────────────────────────────────────────


def _parse_apply_options(payload: dict[str, Any]) -> ApplyOptions:
    """Extract ApplyOptions from the request body, falling back to defaults.

    `options` is optional in the request body; when absent we use the
    library defaults (`prune_orphan_edges=True`, others False). When
    present, only known flags are honoured — extra keys are ignored
    rather than 400'd so the frontend can ship ahead of the backend.
    """
    raw = payload.get("options") or {}
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

    Idempotency check for /apply: covers the crash window between
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

    Centralised so /diff and /apply share the exact same composition —
    /apply MUST NOT trust a client-sent diff (advisor pin #2).
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


# ── /diff (side-effect-free) ────────────────────────────────────────


@topics_bp.route(
    "/api/repos/<name>/topics/proposals/<proposal_id>/topics/<proposed_topic_id>/diff",
    methods=["POST"],
)
def api_repo_topic_proposal_diff(name, proposal_id, proposed_topic_id):
    """Compute the prospective diff for a (proposal, topic, strategy).

    Side-effect-free. Returns the resolved diff + the items that would
    be silently dropped under the supplied (or default) options. UI
    uses this to render the side-by-side diff panel and the resolution
    checkboxes.
    """
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    strategy = payload.get("strategy", "create")
    if strategy not in VALID_STRATEGIES:
        return _error(TopicGraphError(
            f"strategy must be one of {list(VALID_STRATEGIES)}"
        ))
    target = payload.get("target_topic_id")
    options = _parse_apply_options(payload)

    proposal, proposed, approved = _resolve_proposed_topic(
        repo_path, proposal_id, proposed_topic_id,
    )
    if proposal is None:
        return jsonify({"error": "not found"}), 404

    try:
        raw, resolved, dropped = _build_resolved_diff(
            repo_path, approved,
            strategy=strategy, target_topic_id=target, options=options,
        )
    except (ValueError, TopicGraphError) as exc:
        return _error(exc)

    return jsonify({
        "ok": True,
        "diff": serialize_diff(resolved),
        "dropped_items": dropped.to_json(),
        # Echo what we'd get without resolution so the UI can show
        # "here's what we silently filtered" diffs against the raw view.
        "raw_introduced_errors": [serialize_issue(e) for e in raw.introduced_errors],
    })


# ── /apply (commits) ────────────────────────────────────────────────


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
    repo_path: str, proposal_id: str, strategy: str, resolved,
) -> dict[str, str] | None:
    """create/replace promote the proposal's wiki.md to the per-topic wiki
    file under .regin/topics/wiki/<id>.md (the legacy lib path did this via
    `_persist_per_topic_wiki`); merge keeps the target topic's existing
    wiki, so it gets no wiki_pages entry."""
    if strategy not in ("create", "replace") or not resolved.topic_deltas:
        return None
    from lib.topics.core import topic_dir as _topic_dir
    proposal_wiki = _topic_dir(repo_path) / "proposals" / proposal_id / "wiki.md"
    if not proposal_wiki.exists():
        return None
    return {resolved.topic_deltas[0].topic_id: proposal_wiki.read_text()}


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


@topics_bp.route(
    "/api/repos/<name>/topics/proposals/<proposal_id>/topics/<proposed_topic_id>/apply",
    methods=["POST"],
)
def api_repo_topic_proposal_apply(name, proposal_id, proposed_topic_id):
    """Resolve + apply a proposed topic atomically.

    Request body: `{strategy, target_topic_id?, options}`. The diff is
    recomputed server-side — clients MUST NOT send the diff itself.

    Returns:
      200: `{ok, snapshot_id, applied_diff, dropped_items}`
      400 (unresolvable): `{ok=false, error="unresolvable_errors", diff, dropped_items}`
        — UI re-renders the same panel with the unresolved errors
        highlighted so the user can toggle more checkboxes.
    """
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    strategy = payload.get("strategy", "create")
    if strategy not in VALID_STRATEGIES:
        return _error(TopicGraphError(
            f"strategy must be one of {list(VALID_STRATEGIES)}"
        ))
    target = payload.get("target_topic_id")
    options = _parse_apply_options(payload)

    proposal, proposed, approved = _resolve_proposed_topic(
        repo_path, proposal_id, proposed_topic_id,
    )
    if proposal is None:
        return jsonify({"error": "not found"}), 404

    repo = resolve_or_create_repo(repo_path)

    try:
        _raw, resolved, dropped = _build_resolved_diff(
            repo_path, approved,
            strategy=strategy, target_topic_id=target, options=options,
        )
    except (ValueError, TopicGraphError) as exc:
        return _error(exc)

    # Idempotency: a prior apply may have committed before save_proposal
    # ran (crash window), so re-clicking Apply must not 400. We only
    # short-circuit on a true no-op — see _already_applied_noop_snapshot,
    # which gates on the live topic matching what we'd write so a
    # regenerated draft still applies.
    prior_snap_id = _already_applied_noop_snapshot(
        repo.id, proposal_id, resolved, repo_path,
        strategy=strategy, target=target, approved=approved,
    )
    if prior_snap_id is not None:
        return jsonify({
            "ok": True,
            "already_applied": True,
            "snapshot_id": prior_snap_id,
        })

    if _review_state_not_ready(proposal):
        return _error(TopicGraphError(
            "proposal must be marked ready before apply"
        ))

    if not resolved.is_applyable:
        return jsonify({
            "ok": False,
            "error": "unresolvable_errors",
            "diff": serialize_diff(resolved),
            "dropped_items": dropped.to_json(),
        }), 400

    try:
        result = apply_diff(
            repo.id, resolved,
            options=options,
            reason=strategy,
            triggering_run_id=proposal_id,
            wiki_pages=_wiki_pages_for_apply(repo_path, proposal_id, strategy, resolved),
        )
    except (ValueError, TopicGraphError) as exc:
        return _error(exc)

    # Round-trip the edges that downgrade had to prune: if this
    # proposal's run metadata recorded inbound edges that were dropped
    # to make the downgrade legal, patch them back into the siblings
    # now that the topic is in the graph again.
    _restore_pruned_inbound_edges_after_apply(
        repo_path, proposal, resolved,
    )

    from lib.topics.proposals import save_proposal
    _mark_proposal_topic_applied(
        proposal, proposed, approved, resolved, strategy=strategy, target=target,
    )
    save_proposal(repo_path, proposal_id, proposal)

    return jsonify({
        "ok": True,
        "snapshot_id": result.snapshot_id,
        "applied_diff": serialize_diff(resolved),
        "dropped_items": dropped.to_json(),
    })


# ── /audit ──────────────────────────────────────────────────────────


@topics_bp.route("/api/repos/<name>/topics/audit", methods=["GET"])
def api_repo_topic_audit(name):
    """List the live graph's validation issues, grouped by code.

    Reads the approved graph from `topic.json` (still authoritative
    through Phase D); same machinery the diff layer uses to compute
    pre-existing rot. Powers the Audit workspace tab in Phase C.
    """
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    try:
        graph = load_authoritative_graph(repo_path)
    except TopicGraphError as exc:
        return _error(exc, 404)

    issues = audit_graph(graph, repo_path=repo_path)
    by_code: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        by_code.setdefault(issue.code, []).append(serialize_issue(issue))
    return jsonify({
        "ok": True,
        "issues": [serialize_issue(i) for i in issues],
        "by_code": by_code,
        "error_count": sum(1 for i in issues if i.severity == "error"),
        "warning_count": sum(1 for i in issues if i.severity == "warning"),
        "auto_fixable_codes": sorted(AUTO_FIXABLE_CODES),
    })


@topics_bp.route("/api/repos/<name>/topics/audit/fix", methods=["POST"])
def api_repo_topic_audit_fix(name):
    """Auto-fix the unambiguous audit issues.

    Body: `{issue_codes: [str]}`. Only codes in `AUTO_FIXABLE_CODES`
    (currently `graph.dead_ref`, `graph.orphan_edge_target`) are
    actioned; the rest are reported as `skipped_codes`. Returns one
    snapshot per affected topic.

    Why not duplicate_alias: picking which side of a collision to
    drop is destructive and provenance can't always tell you the
    right answer. Surface in the audit list, resolve manually via
    DiffPanel or by editing the proposal that introduced it.
    """
    repo_path = _repo_path_or_404(name)
    if repo_path is None:
        return jsonify({"error": "not found"}), 404
    payload = request.get_json(silent=True) or {}
    requested_codes = set(payload.get("issue_codes") or [])
    if not requested_codes:
        return _error(TopicGraphError("issue_codes is required"))
    skipped_codes = sorted(requested_codes - AUTO_FIXABLE_CODES)
    actionable_codes = requested_codes & AUTO_FIXABLE_CODES
    if not actionable_codes:
        return jsonify({
            "ok": True,
            "snapshot_ids": [],
            "fixed_counts": {},
            "skipped_codes": skipped_codes,
        })

    try:
        graph = load_authoritative_graph(repo_path)
    except TopicGraphError as exc:
        return _error(exc, 404)
    issues = audit_graph(graph, repo_path=repo_path)
    fixes = compose_fix(graph, issues, codes_to_fix=actionable_codes)
    if not fixes:
        return jsonify({
            "ok": True,
            "snapshot_ids": [],
            "fixed_counts": {c: 0 for c in actionable_codes},
            "skipped_codes": skipped_codes,
        })

    repo = resolve_or_create_repo(repo_path)
    snapshot_ids: list[int] = []
    fixed_counts: dict[str, int] = {c: 0 for c in actionable_codes}
    # Apply per-topic; each apply_diff sees the running prospective
    # graph from the previous fix so later issues are computed against
    # the corrected state.
    running_graph = json.loads(json.dumps(graph))
    for topic_id, cleaned, before in fixes:
        prospective = json.loads(json.dumps(running_graph))
        prospective.setdefault("topics", {})[topic_id] = cleaned
        delta = compute_topic_delta(
            topic_id_after=topic_id, kind="replace",
            before=before, after=cleaned,
        )
        diff = GraphDiff(
            topic_deltas=(delta,),
            graph_warnings=(),
            introduced_errors=(),
            valid_strategies_by_topic={topic_id: ("replace",)},
            strategy="replace",
            target_topic_id=None,
            proposed_topic_id=topic_id,
            prospective_graph=prospective,
        )
        try:
            result = apply_diff(repo.id, diff, reason="bulk_fix")
        except (ValueError, TopicGraphError) as exc:
            return _error(exc)
        snapshot_ids.append(result.snapshot_id)
        fixed_counts["graph.dead_ref"] = fixed_counts.get("graph.dead_ref", 0) + len(delta.ref_removes)
        fixed_counts["graph.orphan_edge_target"] = fixed_counts.get("graph.orphan_edge_target", 0) + len(delta.edge_removes)
        running_graph = prospective

    return jsonify({
        "ok": True,
        "snapshot_ids": snapshot_ids,
        "fixed_counts": fixed_counts,
        "skipped_codes": skipped_codes,
    })


# ── /snapshots ──────────────────────────────────────────────────────


def _snapshot_row(snap: GraphSnapshot) -> dict[str, Any]:
    summary = json.loads(snap.diff_summary_json or "{}")
    return {
        "id": snap.id,
        "taken_at": snap.taken_at,
        "reason": snap.reason,
        "triggering_run_id": snap.triggering_run_id,
        "is_latest": bool(snap.is_latest),
        "pinned": bool(snap.pinned),
        "summary": summary,
    }


def _repo_or_404(name: str) -> Repo | None:
    with SessionLocal() as s:
        return s.exec(select(Repo).where(Repo.name == name)).first()


@topics_bp.route("/api/repos/<name>/topics/snapshots", methods=["GET"])
def api_repo_topic_snapshots(name):
    repo = _repo_or_404(name)
    if repo is None:
        return jsonify({"error": "not found"}), 404
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    rows = list_snapshots(repo.id, limit=limit)
    return jsonify({
        "ok": True,
        "snapshots": [_snapshot_row(s) for s in rows],
        "latest_id": next((s.id for s in rows if s.is_latest), None),
    })


@topics_bp.route("/api/repos/<name>/topics/snapshots/<int:snapshot_id>/restore-preview", methods=["GET"])
def api_repo_topic_snapshot_restore_preview(name, snapshot_id):
    """Show what would change if `snapshot_id` were restored.

    Pure read; no mutation. Drives the "Preview before restore" panel.
    """
    repo = _repo_or_404(name)
    if repo is None:
        return jsonify({"error": "not found"}), 404
    with SessionLocal() as s:
        snap = s.get(GraphSnapshot, snapshot_id)
        if snap is None or snap.repo_id != repo.id:
            return jsonify({"error": "not found"}), 404
    try:
        preview = restore_preview(snapshot_id)
    except ValueError as exc:
        return _error(exc)
    return jsonify({"ok": True, "preview": preview})


@topics_bp.route("/api/repos/<name>/topics/snapshots/<int:snapshot_id>/restore", methods=["POST"])
def api_repo_topic_snapshot_restore(name, snapshot_id):
    repo = _repo_or_404(name)
    if repo is None:
        return jsonify({"error": "not found"}), 404
    try:
        with SessionLocal() as s:
            snap = s.get(GraphSnapshot, snapshot_id)
            if snap is None or snap.repo_id != repo.id:
                return jsonify({"error": "not found"}), 404
        restored = restore_snapshot(snapshot_id)
        # After restore, write to the overlay so legacy readers see the new
        # state without mutating the git-tracked base topic.json. The split
        # keeps merge(base, overlay) hash-equal to the restored snapshot.
        from lib.topics.graph_io import export_overlay_to_disk
        restored_graph = json.loads(restored.graph_json)
        export_overlay_to_disk(
            repo.path,
            restored_graph,
            json.loads(restored.wiki_pages_json or "{}"),
        )
        # Refresh the wiki dense index for the restored state in the
        # background — same rationale as apply_diff. Best-effort: a missing
        # embedding model never blocks the restore.
        #
        # Pass `graph=restored_graph` so the bg thread skips
        # `load_authoritative_graph`. Without it, a concurrent
        # apply_diff / restore between the bg thread's snapshot read and
        # disk read makes the bg thread observe `snap_hash != disk_hash`
        # and trigger `_auto_seed_snapshot`, inserting an extra
        # `is_latest=1` row. See `lib/topics/apply.py::_bg_reindex`.
        try:
            import threading
            from lib.patterns.wiki_indexer import index_wikis_best_effort
            threading.Thread(
                target=lambda: index_wikis_best_effort(repo, graph=restored_graph),
                name=f"wiki-index-restore-{repo.id}",
                daemon=True,
            ).start()
        except Exception:  # noqa: BLE001 — restore must not regress on indexing
            pass
    except (ValueError, TopicGraphError) as exc:
        return _error(exc)
    return jsonify({"ok": True, "snapshot": _snapshot_row(restored)})


@topics_bp.route("/api/repos/<name>/topics/snapshots/<int:snapshot_id>/pin", methods=["POST"])
def api_repo_topic_snapshot_pin(name, snapshot_id):
    return _set_pinned(name, snapshot_id, value=1)


@topics_bp.route("/api/repos/<name>/topics/snapshots/<int:snapshot_id>/unpin", methods=["POST"])
def api_repo_topic_snapshot_unpin(name, snapshot_id):
    return _set_pinned(name, snapshot_id, value=0)


def _set_pinned(name: str, snapshot_id: int, *, value: int):
    repo = _repo_or_404(name)
    if repo is None:
        return jsonify({"error": "not found"}), 404
    with SessionLocal() as s:
        snap = s.get(GraphSnapshot, snapshot_id)
        if snap is None or snap.repo_id != repo.id:
            return jsonify({"error": "not found"}), 404
        snap.pinned = value
        s.add(snap)
        s.commit()
        s.refresh(snap)
    return jsonify({"ok": True, "snapshot": _snapshot_row(snap)})


# ── /wiki/reindex ──────────────────────────────────────────────────
#
# Synchronous wiki dense-index refresh for a single repo. The accept
# path auto-runs the same indexer in a background thread, so this
# button exists for: (a) cold-start backfill on a fresh install,
# (b) explicit force-refresh when the user suspects drift, (c) a
# visible feedback signal that the index actually updated.
#
# Sync (not background) is intentional: the user clicked, so they're
# willing to wait through the cold-start ~10s model load on first
# call. Subsequent calls are sub-second per page.


@topics_bp.route("/api/repos/<name>/topics/wiki/reindex", methods=["POST"])
def api_repo_wiki_reindex(name):
    repo = _repo_or_404(name)
    if repo is None:
        return jsonify({"error": "not found"}), 404
    from lib.patterns.wiki_indexer import index_wikis
    from lib.skills import skill_router
    try:
        counts = index_wikis(repo)
    except skill_router.DependencyError as exc:
        return jsonify({
            "ok": False,
            "error": "embedding dependencies missing",
            "detail": str(exc),
        }), 503
    except Exception as exc:  # noqa: BLE001 — surface the failure as 500
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "repo": name, "counts": counts})
